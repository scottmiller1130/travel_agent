#!/usr/bin/env python3
"""
FastAPI web server for the Travel Agent.

Endpoints:
  GET  /                          → serve the chat UI
  POST /api/chat/{session_id}     → SSE stream: tool events + final response
  GET  /api/itinerary/{session_id}→ latest itinerary JSON
  GET  /api/trips/{session_id}    → saved trips JSON
  GET  /api/preferences/{sid}     → user preferences JSON
  POST /api/preferences/{sid}     → update a preference
  POST /api/reset/{session_id}    → clear conversation history (keeps trips/prefs)

Run with:
  uvicorn server:app --reload --port 8000
"""

import asyncio
import json
import logging
import logging.config
import os
import secrets
import shutil
import sys
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.core import TravelAgent
from memory.preferences import PreferenceStore
from memory.trips import TripStore
from memory.sessions import SessionStore

app = FastAPI(title="Travel Agent API")

# ---------------------------------------------------------------------------
# Structured logging — JSON-friendly format with level, time, and request ID
# ---------------------------------------------------------------------------
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"level": _LOG_LEVEL, "handlers": ["console"]},
    # Quieten chatty third-party loggers
    "loggers": {
        "uvicorn.access": {"level": "WARNING"},
        "httpx":          {"level": "WARNING"},
    },
})
log = logging.getLogger("travel_agent")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every API request with a unique request_id and elapsed time."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        request_id = uuid.uuid4().hex[:8]
        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            "%s %s %d %dms rid=%s ip=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            request_id,
            request.client.host if request.client else "-",
        )
        response.headers["X-Request-Id"] = request_id
        return response


app.add_middleware(RequestLoggingMiddleware)

# ---------------------------------------------------------------------------
# Startup validation — fail fast if required environment variables are missing
# ---------------------------------------------------------------------------
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not _ANTHROPIC_API_KEY:
    logging.critical(
        "ANTHROPIC_API_KEY is not set. "
        "Add it to your environment or .env file before starting the server."
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Booking confirmation — agents pause here until the user approves/cancels
# ---------------------------------------------------------------------------
_pending_confirmations: dict[str, dict] = {}  # session_id -> {event, approved}
_conf_lock = threading.Lock()


def _make_confirm_callback(session_id: str):
    """Return a confirm_callback that blocks until the frontend responds."""
    def confirm(_msg: str) -> bool:
        event = threading.Event()
        with _conf_lock:
            _pending_confirmations[session_id] = {"event": event, "approved": False}
        # Block up to 5 minutes for user to interact with the confirmation modal
        event.wait(timeout=300)
        with _conf_lock:
            result = _pending_confirmations.pop(session_id, {}).get("approved", False)
        return result
    return confirm

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "font-src 'self';"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Rate limiting — 20 requests/min per IP on /api/chat
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()
RATE_LIMIT = 20
RATE_WINDOW = 60        # seconds
_MAX_RATE_IPS = 10_000  # evict stale IPs before the dict grows beyond this


def _check_rate_limit(ip: str) -> bool:
    """Return True if allowed, False if rate limit exceeded."""
    now = time.monotonic()
    with _rate_lock:
        # Evict IPs whose last request is older than 2× the window to bound memory.
        if len(_rate_limit_store) > _MAX_RATE_IPS:
            cutoff = now - RATE_WINDOW * 2
            stale = [k for k, v in _rate_limit_store.items() if not v or max(v) < cutoff]
            for k in stale:
                del _rate_limit_store[k]
        timestamps = _rate_limit_store[ip]
        _rate_limit_store[ip] = [t for t in timestamps if now - t < RATE_WINDOW]
        if len(_rate_limit_store[ip]) >= RATE_LIMIT:
            return False
        _rate_limit_store[ip].append(now)
        return True

# ---------------------------------------------------------------------------
# Shared session store (all sessions, all users, persisted to SQLite)
# ---------------------------------------------------------------------------
_session_store = SessionStore()

# In-process agent cache  {session_id: TravelAgent}
# Agents are recreated from persisted state after a server restart.
_agent_cache: dict[str, TravelAgent] = {}
_cache_lock = threading.Lock()


def _get_agent(session_id: str) -> TravelAgent:
    """Return a live TravelAgent for the session, rehydrating from DB if needed."""
    with _cache_lock:
        if session_id in _agent_cache:
            return _agent_cache[session_id]

        agent = TravelAgent(confirm_callback=_make_confirm_callback(session_id))

        # Restore persisted state if available
        saved = _session_store.load(session_id)
        if saved:
            agent.load_conversation(saved["conversation"])
            agent.load_itinerary(saved["itinerary"])

        _agent_cache[session_id] = agent
        return agent


def _save_session(session_id: str, agent: TravelAgent, itinerary=None) -> None:
    """Persist conversation and itinerary after each exchange."""
    _session_store.save(
        session_id,
        agent.get_conversation(),
        itinerary if itinerary is not None else agent.get_itinerary(),
    )


# ---------------------------------------------------------------------------
# Static files (the frontend HTML lives at static/index.html)
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def root():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse(
            "<h1>Frontend not found</h1><p>static/index.html is missing.</p>",
            status_code=404,
        )
    return HTMLResponse(index.read_text())


# ---------------------------------------------------------------------------
# Chat endpoint — SSE stream
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat/{session_id}")
async def chat(session_id: str, body: ChatRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")

    try:
        agent = _get_agent(session_id)
    except Exception as startup_err:
        err_msg = str(startup_err)

        async def err_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"

        return StreamingResponse(
            err_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()
    latest_itinerary: dict[str, object] = {}  # mutable container shared with thread

    def progress_callback(event_type: str, data: dict):
        if event_type == "itinerary_update":
            latest_itinerary["value"] = data.get("itinerary")
        asyncio.run_coroutine_threadsafe(
            event_queue.put({"type": event_type, **data}),
            loop,
        )

    def run_agent():
        try:
            response = agent.chat(body.message, progress_callback=progress_callback)
            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "done", "content": response}),
                loop,
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "error", "message": str(e)}),
                loop,
            )
        finally:
            # Always persist — the conversation sanitizer in load_conversation()
            # will heal any incomplete tool turns if we crashed mid-loop.
            _save_session(session_id, agent, latest_itinerary.get("value"))

    threading.Thread(target=run_agent, daemon=True).start()

    async def event_stream():
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Itinerary (visual board state)
# ---------------------------------------------------------------------------
@app.get("/api/itinerary/{session_id}")
async def get_itinerary(session_id: str):
    saved = _session_store.load(session_id)
    itinerary = saved["itinerary"] if saved else None
    # Also check live agent cache in case it was updated this session
    with _cache_lock:
        if session_id in _agent_cache:
            live = _agent_cache[session_id].get_itinerary()
            if live:
                itinerary = live
    return JSONResponse({"itinerary": itinerary})


class ItineraryUpdate(BaseModel):
    itinerary: dict


@app.post("/api/itinerary/{session_id}")
async def save_itinerary(session_id: str, body: ItineraryUpdate):
    """Persist an itinerary from the frontend (drag-and-drop reorder, import)."""
    _session_store.save_itinerary(session_id, body.itinerary)
    # Also update the live agent if it's in cache
    with _cache_lock:
        if session_id in _agent_cache:
            _agent_cache[session_id].load_itinerary(body.itinerary)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
@app.get("/api/trips/{session_id}")
async def get_trips(session_id: str):
    trips = TripStore().get_all_trips()
    return JSONResponse({"trips": trips})


@app.post("/api/trips/{session_id}")
async def save_trip(session_id: str, request: Request):
    """Permanently save the current session itinerary to the trip store."""
    if not _session_store.exists(session_id):
        raise HTTPException(status_code=403, detail="Unknown session.")
    data = _session_store.load(session_id)
    itinerary = data and data.get("itinerary")
    if not itinerary:
        raise HTTPException(status_code=404, detail="No itinerary to save.")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    # Allow the client to supply a custom name; fall back to destination
    name = (body.get("name") or "").strip() or itinerary.get("destination") or "My Trip"
    itinerary["name"] = name
    trip_id = TripStore().save_trip(itinerary)
    return JSONResponse({"status": "ok", "trip_id": trip_id, "name": name})


@app.delete("/api/trips/{session_id}/{trip_id}")
async def delete_trip(session_id: str, trip_id: str):
    """Delete a saved trip by ID."""
    if not _session_store.exists(session_id):
        raise HTTPException(status_code=403, detail="Unknown session.")
    store = TripStore()
    if not store.get_trip(trip_id):
        raise HTTPException(status_code=404, detail="Trip not found.")
    store.delete_trip(trip_id)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
@app.get("/api/preferences/{session_id}")
async def get_preferences(session_id: str):
    return JSONResponse(PreferenceStore().get_all())


VALID_PREF_KEYS = {
    "preferred_airlines", "avoided_airlines", "seat_preference",
    "cabin_class", "hotel_min_stars", "max_budget_per_day_usd",
    "dietary_restrictions", "accessibility_needs", "preferred_activities",
    "avoided_activities", "travel_pace", "home_airport", "home_city",
    "currency", "name", "email",
}


class PrefUpdate(BaseModel):
    key: str
    value: object


@app.post("/api/preferences/{session_id}")
async def set_preference(session_id: str, body: PrefUpdate):
    if body.key not in VALID_PREF_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid preference key: {body.key!r}")
    PreferenceStore().set(body.key, body.value)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Trip sharing — read-only itinerary view via one-time-safe token
# ---------------------------------------------------------------------------
@app.post("/api/share/{session_id}")
async def create_share_link(session_id: str, request: Request):
    saved = _session_store.load(session_id)
    if not saved or not saved.get("itinerary"):
        raise HTTPException(status_code=404, detail="No itinerary to share for this session.")
    token = _session_store.create_share_token(session_id)
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse({"status": "ok", "share_url": f"{base_url}/s/{token}"})


@app.get("/s/{token}", response_class=HTMLResponse)
async def shared_itinerary(token: str):
    """Render a rich read-only itinerary view for a share token."""
    data = _session_store.get_session_for_token(token)
    if not data or not data.get("itinerary"):
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;padding:40px'>Share link not found or itinerary is empty.</h2>",
            status_code=404,
        )
    it = data["itinerary"]
    dest = it.get("destination") or (
        " → ".join(it["destinations"]) if it.get("destinations") else "Trip"
    )
    date_range = " → ".join(filter(None, [it.get("start_date"), it.get("end_date")]))

    # ── budget breakdown ──────────────────────────────────────────────────────
    budget = it.get("budget") or {}
    budget_total = sum(budget.values())
    BUDGET_META = {
        "flights":    ("✈", "#0ea5e9", "#e0f2fe"),
        "hotels":     ("🏨", "#0d9488", "#ccfbf1"),
        "activities": ("🗺", "#f97316", "#ffedd5"),
        "food":       ("🍽", "#f59e0b", "#fef3c7"),
        "transport":  ("🚗", "#6366f1", "#ede9fe"),
        "other":      ("📦", "#64748b", "#f1f5f9"),
    }
    budget_cards_html = ""
    if budget_total:
        for cat, amt in budget.items():
            if not amt:
                continue
            icon, color, bg = BUDGET_META.get(cat, ("💰", "#64748b", "#f1f5f9"))
            pct = round(amt / budget_total * 100)
            budget_cards_html += (
                f"<div class='bcard' style='--c:{color};--bg:{bg}'>"
                f"<span class='bicon'>{icon}</span>"
                f"<span class='bcat'>{_safe(cat.title())}</span>"
                f"<span class='bamt'>${amt:,.0f}</span>"
                f"<div class='bbar-bg'><div class='bbar-fill' style='width:{pct}%'></div></div>"
                f"<span class='bpct'>{pct}%</span>"
                f"</div>"
            )

    # ── trip stats ────────────────────────────────────────────────────────────
    days_list = it.get("days", [])
    num_days = len(days_list)
    travelers = it.get("travelers", 0)
    all_items = [item for day in days_list for item in day.get("items", [])]
    flights_count   = sum(1 for i in all_items if i.get("type") == "flight")
    hotels_count    = sum(1 for i in all_items if i.get("type") == "hotel")
    activity_count  = sum(1 for i in all_items if i.get("type") not in ("flight", "hotel", ""))
    stats_html = ""
    stats = []
    if num_days:      stats.append(("📅", str(num_days), "days"))
    if travelers:     stats.append(("👤", str(travelers), "traveler(s)"))
    if flights_count: stats.append(("✈", str(flights_count), "flight(s)"))
    if hotels_count:  stats.append(("🏨", str(hotels_count), "hotel night(s)"))
    if activity_count:stats.append(("🗺", str(activity_count), "activities"))
    if budget_total:  stats.append(("💰", f"${budget_total:,.0f}", "est. total"))
    for icon, val, label in stats:
        stats_html += f"<div class='stat'><span class='sicon'>{icon}</span><strong>{_safe(val)}</strong><span>{_safe(label)}</span></div>"

    # ── item type metadata ────────────────────────────────────────────────────
    ITEM_META = {
        "flight":    ("✈",  "#0ea5e9", "#e0f2fe"),
        "hotel":     ("🏨", "#0d9488", "#ccfbf1"),
        "activity":  ("🗺", "#f97316", "#ffedd5"),
        "food":      ("🍽", "#f59e0b", "#fef3c7"),
        "restaurant":("🍽", "#f59e0b", "#fef3c7"),
        "transport": ("🚗", "#6366f1", "#ede9fe"),
        "transfer":  ("🚗", "#6366f1", "#ede9fe"),
    }

    # ── days ──────────────────────────────────────────────────────────────────
    days_html = ""
    for i, day in enumerate(days_list):
        try:
            from datetime import date as dt_date
            dlbl = dt_date.fromisoformat(day.get("date", "")).strftime("%A, %b %-d")
        except Exception:
            dlbl = f"Day {i + 1}"
        if day.get("label"):
            dlbl += f" — {day['label']}"

        # weather strip
        weather_html = ""
        wx = day.get("weather") or {}
        if wx:
            cond  = _safe(wx.get("condition", ""))
            hi    = wx.get("high_c") or wx.get("high")
            lo    = wx.get("low_c")  or wx.get("low")
            emoji = wx.get("emoji", "🌤")
            temp  = f"{hi}°/{lo}°C" if hi and lo else (f"{hi}°C" if hi else "")
            weather_html = f"<div class='wx'>{emoji} {_safe(temp)} {cond}</div>"

        items_html = ""
        for item in day.get("items", []):
            itype = (item.get("type") or "").lower()
            icon, color, bg = ITEM_META.get(itype, ("📌", "#64748b", "#f1f5f9"))
            time_str  = _safe(item.get("time", ""))
            title_str = _safe(item.get("title", ""))
            price     = item.get("price_usd")
            price_html = f"<span class='iprice'>${price:,.0f}</span>" if price else ""
            notes     = item.get("notes") or item.get("description") or ""
            notes_html = f"<div class='inotes'>{_safe(notes)}</div>" if notes else ""
            airline   = item.get("airline") or item.get("carrier") or ""
            airline_html = f"<span class='itag'>{_safe(airline)}</span>" if airline else ""
            duration  = item.get("duration") or ""
            dur_html   = f"<span class='itag'>{_safe(duration)}</span>" if duration else ""
            status    = item.get("status") or ""
            STATUS_COLORS = {"confirmed": "#10b981", "suggested": "#f59e0b", "alternative": "#6366f1"}
            status_html = ""
            if status:
                sc = STATUS_COLORS.get(status.lower(), "#64748b")
                status_html = f"<span class='istatus' style='background:{sc}20;color:{sc}'>{_safe(status.title())}</span>"

            time_html  = f"<span class='itime'>{time_str}</span>" if time_str else ""
            tags_html  = f"<div class='itags'>{airline_html}{dur_html}</div>" if (airline or duration) else ""
            items_html += (
                f"<div class='icard' style='--ic:{color};--ibg:{bg}'>"
                f"<span class='iicon'>{icon}</span>"
                f"<div class='idetails'>"
                f"<div class='itop'>{time_html}<span class='ititle'>{title_str}</span>{price_html}{status_html}</div>"
                f"{tags_html}"
                f"{notes_html}"
                f"</div>"
                f"</div>"
            )

        days_html += (
            f"<div class='day'>"
            f"<div class='day-header'><span class='day-label'>{_safe(dlbl)}</span>{weather_html}</div>"
            f"<div class='day-items'>{items_html}</div>"
            f"</div>"
        )

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>✈ {_safe(dest)} — Travel Itinerary</title>
<style>
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  :root{{
    --sky:#0ea5e9;--sky-d:#0284c7;--sky-l:#e0f2fe;
    --teal:#0d9488;--teal-l:#ccfbf1;
    --border:#e2e8f0;--bg:#f0f9ff;--surface:#fff;
    --text:#0f172a;--text-2:#334155;--text-3:#64748b;
  }}
  html{{scroll-behavior:smooth}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.6}}

  /* hero */
  .hero{{background:linear-gradient(135deg,#0ea5e9 0%,#0d9488 60%,#7c3aed 100%);color:#fff;padding:48px 24px 56px;position:relative;overflow:hidden;text-align:center}}
  .hero::before{{content:'✈';position:absolute;font-size:260px;opacity:.07;top:50%;left:50%;transform:translate(-50%,-50%) rotate(15deg);line-height:1;pointer-events:none}}
  .hero h1{{font-size:clamp(26px,5vw,44px);font-weight:900;letter-spacing:-1px;margin-bottom:8px;text-shadow:0 2px 12px rgba(0,0,0,.2)}}
  .hero .dates{{font-size:15px;opacity:.85;margin-bottom:20px}}
  .wave{{display:block;width:100%;height:48px;background:var(--bg);clip-path:ellipse(55% 100% at 50% 100%);margin-top:-1px}}

  /* stats bar */
  .stats-bar{{display:flex;gap:12px;flex-wrap:wrap;justify-content:center;background:var(--surface);border-bottom:1px solid var(--border);padding:16px 24px}}
  .stat{{display:flex;flex-direction:column;align-items:center;gap:2px;min-width:72px}}
  .sicon{{font-size:18px}}
  .stat strong{{font-size:16px;font-weight:800;color:var(--text)}}
  .stat span{{font-size:11px;color:var(--text-3);font-weight:600;text-transform:uppercase;letter-spacing:.05em}}

  /* layout */
  .page{{max-width:780px;margin:0 auto;padding:32px 16px}}

  /* budget */
  .budget-section{{background:var(--surface);border:1.5px solid var(--border);border-radius:16px;padding:24px;margin-bottom:28px;box-shadow:0 4px 12px rgba(0,0,0,.05)}}
  .section-title{{font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;color:var(--text-3);margin-bottom:14px}}
  .bcards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px}}
  .bcard{{background:var(--bg);border:1.5px solid var(--border);border-radius:12px;padding:12px;position:relative;overflow:hidden}}
  .bcard::before{{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c)}}
  .bicon{{font-size:18px;display:block;margin-bottom:4px}}
  .bcat{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text-3);display:block}}
  .bamt{{font-size:18px;font-weight:900;color:var(--c);display:block;margin:2px 0}}
  .bbar-bg{{height:4px;background:var(--border);border-radius:2px;margin:6px 0 4px}}
  .bbar-fill{{height:4px;background:var(--c);border-radius:2px}}
  .bpct{{font-size:10px;color:var(--text-3);font-weight:600}}

  /* days */
  .day{{background:var(--surface);border:1.5px solid var(--border);border-radius:16px;margin-bottom:16px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.04)}}
  .day-header{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;background:linear-gradient(90deg,var(--sky-l),var(--teal-l));border-bottom:1px solid var(--border);gap:10px;flex-wrap:wrap}}
  .day-label{{font-size:14px;font-weight:800;color:var(--sky-d)}}
  .wx{{font-size:12px;color:var(--text-3);background:#fff;border:1px solid var(--border);border-radius:20px;padding:3px 10px;font-weight:600}}
  .day-items{{padding:12px;display:flex;flex-direction:column;gap:8px}}

  /* item cards */
  .icard{{display:flex;gap:10px;background:var(--ibg);border:1.5px solid var(--border);border-left:4px solid var(--ic);border-radius:10px;padding:10px 12px;align-items:flex-start}}
  .iicon{{font-size:18px;flex-shrink:0;margin-top:1px}}
  .idetails{{flex:1;min-width:0}}
  .itop{{display:flex;align-items:center;flex-wrap:wrap;gap:6px}}
  .itime{{font-family:'SF Mono','Fira Code',monospace;font-size:11px;font-weight:700;color:var(--ic);background:#fff;border:1px solid var(--border);border-radius:5px;padding:1px 6px;flex-shrink:0}}
  .ititle{{font-size:14px;font-weight:700;color:var(--text);flex:1}}
  .iprice{{font-size:12px;font-weight:800;color:var(--teal);background:var(--teal-l);border-radius:5px;padding:1px 7px;flex-shrink:0}}
  .istatus{{font-size:10px;font-weight:700;border-radius:5px;padding:1px 7px;flex-shrink:0;text-transform:uppercase;letter-spacing:.05em}}
  .itags{{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}}
  .itag{{font-size:11px;color:var(--text-3);background:#fff;border:1px solid var(--border);border-radius:5px;padding:1px 7px;font-weight:600}}
  .inotes{{margin-top:5px;font-size:12px;color:var(--text-3);line-height:1.5}}

  /* print btn */
  .print-btn{{display:flex;align-items:center;gap:6px;background:linear-gradient(135deg,var(--sky),var(--teal));color:#fff;border:none;border-radius:10px;padding:10px 18px;font-size:13px;font-weight:700;cursor:pointer;margin:0 auto 28px;box-shadow:0 4px 12px rgba(14,165,233,.35)}}
  .print-btn:hover{{opacity:.9}}

  /* footer */
  footer{{margin-top:40px;text-align:center;font-size:12px;color:var(--text-3);border-top:1px solid var(--border);padding-top:20px;padding-bottom:40px}}
  footer a{{color:var(--sky-d);text-decoration:none;font-weight:600}}

  @media print{{
    .print-btn,.stats-bar,.wave{{display:none}}
    .hero{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
    body{{background:#fff}}
    .day,.budget-section{{box-shadow:none;border:1px solid #ddd}}
  }}
  @media(max-width:500px){{
    .hero{{padding:36px 16px 44px}}
    .bcards{{grid-template-columns:1fr 1fr}}
  }}
</style>
</head>
<body>

<div class="hero">
  <h1>✈ {_safe(dest)}</h1>
  <div class="dates">{_safe(date_range)}</div>
</div>
<div class="wave"></div>

<div class="stats-bar">{stats_html}</div>

<div class="page">

  <button class="print-btn" onclick="window.print()">🖨 Print / Save as PDF</button>

  {f'''<div class="budget-section">
    <div class="section-title">💰 Budget Breakdown</div>
    <div class="bcards">{budget_cards_html}</div>
    <p style="margin-top:14px;font-size:13px;color:var(--text-3)">
      <strong style="color:var(--text)">Estimated total: ${budget_total:,.0f}</strong>
      {"&nbsp;·&nbsp;" + _safe(str(travelers)) + " traveler(s)" if travelers else ""}
    </p>
  </div>''' if budget_cards_html else ""}

  <div class="section-title" style="margin-bottom:12px">📅 Day-by-Day Itinerary</div>
  {days_html}

</div>

<footer>
  Shared via <a href="/">Travel Agent</a> &nbsp;·&nbsp; Read-only view
</footer>

</body></html>""")


def _safe(s: object) -> str:
    """HTML-escape a value for safe inline insertion."""
    import html
    return html.escape(str(s)) if s else ""


# ---------------------------------------------------------------------------
# Session management — server-generated IDs prevent client forgery
# ---------------------------------------------------------------------------
@app.post("/api/session/new")
async def new_session():
    session_id = secrets.token_urlsafe(32)
    _session_store.create(session_id)
    return JSONResponse({"session_id": session_id})


# ---------------------------------------------------------------------------
# Reset conversation (keeps trips and preferences)
# ---------------------------------------------------------------------------
@app.post("/api/reset/{session_id}")
async def reset(session_id: str):
    with _cache_lock:
        if session_id in _agent_cache:
            _agent_cache[session_id].reset()
    _session_store.delete(session_id)
    return JSONResponse({"status": "ok", "message": "Conversation reset."})


# ---------------------------------------------------------------------------
# Booking confirmation endpoints (called by the frontend modal)
# ---------------------------------------------------------------------------
@app.post("/api/booking/confirm/{session_id}")
async def booking_confirm(session_id: str):
    with _conf_lock:
        pending = _pending_confirmations.get(session_id)
    if pending:
        pending["approved"] = True
        pending["event"].set()
    return JSONResponse({"status": "ok"})


@app.post("/api/booking/cancel/{session_id}")
async def booking_cancel(session_id: str):
    with _conf_lock:
        pending = _pending_confirmations.get(session_id)
    if pending:
        pending["approved"] = False
        pending["event"].set()
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Health check — returns minimal info to avoid leaking configuration
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Automated DB backup — copies sessions.db daily, keeps last 7 snapshots
# ---------------------------------------------------------------------------
def _backup_db() -> None:
    """Copy sessions.db to a timestamped backup and expire old sessions. Runs daily."""
    from memory.sessions import DB_PATH
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    while True:
        time.sleep(86400)  # sleep 24 hours
        try:
            # Expire sessions idle for more than 30 days
            deleted = _session_store.expire_old_sessions(days=30)
            if deleted:
                log.info("Expired %d stale sessions (>30 days idle)", deleted)
        except Exception as exc:
            log.error("Session expiry failed: %s", exc)
        try:
            if DB_PATH.exists():
                stamp = time.strftime("%Y%m%d_%H%M%S")
                dest = backup_dir / f"sessions_{stamp}.db"
                shutil.copy2(DB_PATH, dest)
                # Prune to keep only the 7 most recent backups
                backups = sorted(backup_dir.glob("sessions_*.db"))
                for old in backups[:-7]:
                    old.unlink(missing_ok=True)
        except Exception as exc:
            log.error("DB backup failed: %s", exc)


threading.Thread(target=_backup_db, daemon=True, name="db-backup").start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
