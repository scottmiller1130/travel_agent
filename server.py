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
from memory.users import UserStore
from memory.workspaces import WorkspaceStore

_workspace_store = WorkspaceStore()

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
# Auth — Clerk JWT verification via JWKS (RS256)
# Set CLERK_JWKS_URL and CLERK_PUBLISHABLE_KEY in environment to enable auth.
# Without these, the app runs in anonymous mode (backward compatible).
# ---------------------------------------------------------------------------
_clerk_jwks_client = None
_clerk_jwks_lock = threading.Lock()

def _get_jwks_client():
    """Lazily create a PyJWKClient that auto-refreshes Clerk's public keys."""
    global _clerk_jwks_client
    jwks_url = os.getenv("CLERK_JWKS_URL", "").strip()
    if not jwks_url:
        return None
    with _clerk_jwks_lock:
        if _clerk_jwks_client is None:
            try:
                import jwt as _jwt
                from jwt import PyJWKClient as _PyJWKClient
                _clerk_jwks_client = _PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)
            except Exception as exc:
                log.warning("Failed to initialise JWKS client: %s", exc)
    return _clerk_jwks_client


def _verify_clerk_token(token: str) -> dict | None:
    """Verify a Clerk JWT and return the payload, or None if invalid."""
    client = _get_jwks_client()
    if not client:
        return None
    try:
        import jwt as _jwt
        signing_key = client.get_signing_key_from_jwt(token)
        payload = _jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return payload
    except Exception:
        return None


def _user_from_request(request: Request) -> dict | None:
    """Extract and verify the Clerk JWT from the Authorization header.
    Returns a minimal user dict {user_id, email, name} or None for anonymous."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    payload = _verify_clerk_token(auth[7:])
    if not payload:
        return None
    return {
        "user_id": payload.get("sub", ""),
        "email":   payload.get("email", ""),
        "name":    payload.get("name", ""),
    }


_user_store = UserStore()


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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev https://clerk.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "connect-src 'self' https://*.clerk.accounts.dev https://clerk.com https://api.clerk.com; "
            "img-src 'self' data: https://*.clerk.com https://*.gravatar.com; "
            "font-src 'self' https://fonts.gstatic.com;"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Rate limiting — per-user (authenticated) or per-IP (anonymous)
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()
RATE_LIMIT_ANON = 20   # requests/min per IP (anonymous)
RATE_LIMIT_AUTH = 40   # requests/min per user_id (authenticated, higher trust)
RATE_WINDOW = 60
_MAX_RATE_KEYS = 10_000


def _check_rate_limit(key: str, limit: int = RATE_LIMIT_ANON) -> bool:
    """Return True if allowed. Key should be user_id when authenticated, IP otherwise."""
    now = time.monotonic()
    with _rate_lock:
        if len(_rate_limit_store) > _MAX_RATE_KEYS:
            cutoff = now - RATE_WINDOW * 2
            stale = [k for k, v in _rate_limit_store.items() if not v or max(v) < cutoff]
            for k in stale:
                del _rate_limit_store[k]
        _rate_limit_store[key] = [t for t in _rate_limit_store[key] if now - t < RATE_WINDOW]
        if len(_rate_limit_store[key]) >= limit:
            return False
        _rate_limit_store[key].append(now)
        return True

# ---------------------------------------------------------------------------
# Shared session store (all sessions, all users, persisted to SQLite)
# ---------------------------------------------------------------------------
_session_store = SessionStore()

# In-process agent cache  {session_id: TravelAgent}
# Agents are recreated from persisted state after a server restart.
_agent_cache: dict[str, TravelAgent] = {}
_cache_lock = threading.Lock()


def _get_agent(session_id: str, user_id: str | None = None) -> TravelAgent:
    """Return a live TravelAgent for the session, rehydrating from DB if needed."""
    with _cache_lock:
        if session_id in _agent_cache:
            agent = _agent_cache[session_id]
            # Keep user_id in sync if it was just established (e.g. user logged in)
            if user_id and not agent._user_id:
                agent._user_id = user_id
            return agent

        agent = TravelAgent(
            confirm_callback=_make_confirm_callback(session_id),
            user_id=user_id,
        )

        # Restore persisted state if available
        saved = _session_store.load(session_id)
        if saved:
            agent.load_conversation(saved["conversation"])
            agent.load_itinerary(saved["itinerary"])

        _agent_cache[session_id] = agent
        return agent


def _require_session_access(session_id: str, auth_user: dict | None) -> None:
    """Raise 403/404 if the session doesn't exist or the authenticated user doesn't own it."""
    if not _session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    if auth_user and auth_user.get("user_id"):
        if not _session_store.owns(session_id, auth_user["user_id"]):
            raise HTTPException(status_code=403, detail="Access denied.")


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


@app.get("/help", response_class=HTMLResponse)
async def user_guide():
    guide = Path(__file__).parent / "docs" / "user-guide.html"
    if not guide.exists():
        return HTMLResponse("<h1>User guide not found</h1>", status_code=404)
    return HTMLResponse(guide.read_text())


# ---------------------------------------------------------------------------
# Chat endpoint — SSE stream
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat/{session_id}")
async def chat(session_id: str, body: ChatRequest, request: Request):
    auth_user = _user_from_request(request)
    uid = auth_user["user_id"] if auth_user else None

    # Rate limit: use user_id when authenticated (higher limit), IP otherwise
    client_ip = request.client.host if request.client else "unknown"
    rl_key   = uid if uid else client_ip
    rl_limit = RATE_LIMIT_AUTH if uid else RATE_LIMIT_ANON
    if not _check_rate_limit(rl_key, rl_limit):
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")

    # Session ownership check
    _require_session_access(session_id, auth_user)

    # Enforce plan limits for authenticated users
    if uid:
        user_rec = _user_store.get(uid)
        if user_rec:
            usage = _user_store.get_usage(uid)
            if not _user_store.within_limit(user_rec, "chat_turns", usage):
                plan = user_rec["plan"]
                from memory.users import PLAN_LIMITS
                cap = PLAN_LIMITS[plan]["chat_turns"]
                raise HTTPException(
                    status_code=402,
                    detail=f"Monthly chat limit reached ({cap} turns on {plan} plan). Upgrade to Pro for unlimited chats.",
                )
            _user_store.increment_chat(uid)

    try:
        agent = _get_agent(session_id, user_id=uid)
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


@app.delete("/api/itinerary/{session_id}")
async def clear_itinerary(session_id: str, request: Request):
    """Clear the current itinerary for a session."""
    auth_user = _user_from_request(request)
    _require_session_access(session_id, auth_user)
    _session_store.clear_itinerary(session_id)
    with _cache_lock:
        if session_id in _agent_cache:
            _agent_cache[session_id].load_itinerary(None)
    return JSONResponse({"status": "ok"})


@app.post("/api/itinerary/{session_id}")
async def save_itinerary(session_id: str, body: ItineraryUpdate, request: Request):
    """Persist an itinerary from the frontend (drag-and-drop reorder, import)."""
    auth_user = _user_from_request(request)
    _require_session_access(session_id, auth_user)
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
async def get_trips(session_id: str, request: Request):
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    trips = TripStore().get_all_trips(user_id=user_id)
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
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    trip_id = TripStore().save_trip(itinerary, user_id=user_id)
    return JSONResponse({"status": "ok", "trip_id": trip_id, "name": name})


@app.delete("/api/trips/{session_id}/{trip_id}")
async def delete_trip(session_id: str, trip_id: str, request: Request):
    """Delete a saved trip by ID."""
    if not _session_store.exists(session_id):
        raise HTTPException(status_code=403, detail="Unknown session.")
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    store = TripStore()
    if not store.get_trip(trip_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="Trip not found.")
    store.delete_trip(trip_id, user_id=user_id)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
@app.get("/api/preferences/{session_id}")
async def get_preferences(session_id: str, request: Request):
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    return JSONResponse(PreferenceStore().get_all(user_id=user_id))


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
async def set_preference(session_id: str, body: PrefUpdate, request: Request):
    if body.key not in VALID_PREF_KEYS:
        raise HTTPException(status_code=400, detail=f"Invalid preference key: {body.key!r}")
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    PreferenceStore().set(body.key, body.value, user_id=user_id)
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
    """Render a rich, accordion read-only itinerary view for a share token."""
    from datetime import date as _dt_date
    import html as _html

    data = _session_store.get_session_for_token(token)
    if not data or not data.get("itinerary"):
        return HTMLResponse(
            "<h2 style='font-family:sans-serif;padding:40px'>Share link not found or itinerary is empty.</h2>",
            status_code=404,
        )
    it = data["itinerary"]

    # ── destination & dates ───────────────────────────────────────────────────
    dests = it.get("destinations") or []
    dest = it.get("destination") or (" → ".join(dests) if dests else "Trip")
    start = it.get("start_date", "")
    end   = it.get("end_date", "")
    try:
        start_fmt = _dt_date.fromisoformat(start).strftime("%b %-d, %Y") if start else ""
        end_fmt   = _dt_date.fromisoformat(end).strftime("%b %-d, %Y")   if end   else ""
    except Exception:
        start_fmt, end_fmt = start, end
    date_range = f"{start_fmt} → {end_fmt}" if start_fmt or end_fmt else ""
    travelers  = it.get("travelers") or 0

    # ── budget — exclude synthetic "total" / "grand_total" keys to avoid double-counting ──
    KNOWN_CATS = {"flights", "hotels", "activities", "food", "transport", "other"}
    raw_budget = it.get("budget") or {}
    budget = {k: v for k, v in raw_budget.items()
              if k in KNOWN_CATS and isinstance(v, (int, float)) and v}
    budget_total = sum(budget.values())

    BUDGET_META = {
        "flights":    ("✈",  "#0ea5e9", "#e0f2fe", "#bae6fd"),
        "hotels":     ("🏨", "#0d9488", "#ccfbf1", "#99f6e4"),
        "activities": ("🗺", "#f97316", "#ffedd5", "#fed7aa"),
        "food":       ("🍽", "#f59e0b", "#fef3c7", "#fde68a"),
        "transport":  ("🚗", "#6366f1", "#ede9fe", "#ddd6fe"),
        "other":      ("📦", "#64748b", "#f1f5f9", "#e2e8f0"),
    }

    budget_cards_html = ""
    for cat, amt in budget.items():
        icon, color, bg, _ = BUDGET_META.get(cat, ("💰", "#64748b", "#f1f5f9", "#e2e8f0"))
        pct = round(amt / budget_total * 100) if budget_total else 0
        per_person_html = ""
        if travelers and travelers > 1:
            per_amt = round(amt / travelers)
            per_person_html = f"<span class='bper'>${per_amt:,} / person</span>"
        budget_cards_html += (
            f"<div class='bcard' style='--c:{color};--bg:{bg}'>"
            f"<span class='bicon'>{icon}</span>"
            f"<span class='bcat'>{_safe(cat.title())}</span>"
            f"<span class='bamt'>${amt:,.0f}</span>"
            f"{per_person_html}"
            f"<div class='bbar-bg'><div class='bbar-fill' style='width:{pct}%'></div></div>"
            f"<span class='bpct'>{pct}%</span>"
            f"</div>"
        )

    # ── trip stats ────────────────────────────────────────────────────────────
    days_list = it.get("days") or []
    num_days  = len(days_list)
    all_items = [item for day in days_list for item in (day.get("items") or [])]
    flights_n  = sum(1 for i in all_items if i.get("type") == "flight")
    hotels_n   = sum(1 for i in all_items if i.get("type") == "hotel")
    acts_n     = sum(1 for i in all_items if i.get("type") == "activity")

    stats = []
    if num_days:     stats.append(("📅", str(num_days),             "days"))
    if travelers:    stats.append(("👥", str(travelers),            "traveler(s)"))
    if flights_n:    stats.append(("✈",  str(flights_n),           "flight(s)"))
    if hotels_n:     stats.append(("🏨", str(hotels_n),            "hotel night(s)"))
    if acts_n:       stats.append(("🗺", str(acts_n),              "activities"))
    if budget_total: stats.append(("💰", f"${budget_total:,.0f}",  "est. total"))
    if budget_total and travelers and travelers > 1:
        per_person_total = round(budget_total / travelers)
        stats.append(("👤", f"${per_person_total:,.0f}", "per person"))
    stats_html = "".join(
        f"<div class='stat'><span class='sicon'>{ic}</span>"
        f"<strong>{_safe(v)}</strong><span>{_safe(lbl)}</span></div>"
        for ic, v, lbl in stats
    )

    # ── item type meta ────────────────────────────────────────────────────────
    ITEM_META = {
        "flight":     ("✈",  "#0ea5e9", "#e0f2fe"),
        "hotel":      ("🏨", "#0d9488", "#ccfbf1"),
        "activity":   ("🗺", "#f97316", "#ffedd5"),
        "food":       ("🍽", "#f59e0b", "#fef3c7"),
        "restaurant": ("🍽", "#f59e0b", "#fef3c7"),
        "transport":  ("🚗", "#6366f1", "#ede9fe"),
        "transfer":   ("🚗", "#6366f1", "#ede9fe"),
        "free_time":  ("☀️", "#10b981", "#d1fae5"),
    }
    STATUS_COLORS = {
        "confirmed":   "#10b981",
        "suggested":   "#f59e0b",
        "alternative": "#6366f1",
    }

    # ── days ──────────────────────────────────────────────────────────────────
    days_html = ""
    for i, day in enumerate(days_list):
        # date label
        date_str = day.get("date", "")
        try:
            dlbl = _dt_date.fromisoformat(date_str).strftime("%A, %b %-d")
        except Exception:
            dlbl = f"Day {i + 1}"
        theme = day.get("label") or ""
        # item count for collapsed preview
        n_items = len(day.get("items") or [])

        # weather — schema uses temp_high / temp_low (Celsius)
        weather_html = ""
        wx = day.get("weather") or {}
        if wx:
            cond  = wx.get("condition", "")
            hi    = wx.get("temp_high") or wx.get("high_c") or wx.get("high")
            lo    = wx.get("temp_low")  or wx.get("low_c")  or wx.get("low")
            emoji = wx.get("emoji", "🌤")
            parts = [emoji]
            if hi and lo:
                parts.append(f"{int(hi)}°/{int(lo)}°C")
            elif hi:
                parts.append(f"{int(hi)}°C")
            if cond:
                parts.append(cond)
            weather_html = f"<span class='wx'>{'&nbsp;'.join(_safe(p) for p in parts)}</span>"

        # items
        items_html = ""
        for item in (day.get("items") or []):
            itype = (item.get("type") or "").lower()
            icon, color, bg = ITEM_META.get(itype, ("📌", "#64748b", "#f1f5f9"))

            time_str   = _safe(item.get("time") or "")
            end_time   = _safe(item.get("end_time") or "")
            title_str  = _safe(item.get("title") or "Untitled")
            subtitle   = _safe(item.get("subtitle") or "")  # airline, address, etc.
            notes      = _safe(item.get("notes") or "")
            dur_h      = item.get("duration_hours")
            price      = item.get("price_usd")
            status     = (item.get("status") or "").lower()

            time_range = time_str
            if time_str and end_time:
                time_range = f"{time_str}–{end_time}"
            time_html  = f"<span class='itime'>{time_range}</span>" if time_range else ""

            dur_html   = (f"<span class='itag'>⏱ {dur_h:.0f}h</span>"
                          if dur_h else "")
            sub_html   = f"<span class='itag'>{subtitle}</span>" if subtitle else ""
            tags_html  = f"<div class='itags'>{sub_html}{dur_html}</div>" if (subtitle or dur_h) else ""

            price_html = (f"<span class='iprice'>${price:,.0f}</span>"
                          if isinstance(price, (int, float)) and price else "")

            sc = STATUS_COLORS.get(status, "")
            status_html = (
                f"<span class='istatus' style='background:{sc}20;color:{sc}'>{_safe(status.title())}</span>"
                if sc else ""
            )
            notes_html = f"<div class='inotes'>{notes}</div>" if notes else ""

            items_html += (
                f"<div class='icard' style='--ic:{color};--ibg:{bg}'>"
                f"<span class='iicon'>{icon}</span>"
                f"<div class='idetails'>"
                f"<div class='itop'>{time_html}<span class='ititle'>{title_str}</span>"
                f"{price_html}{status_html}</div>"
                f"{tags_html}{notes_html}"
                f"</div></div>"
            )

        # accordion — first day starts open
        open_attr = " open" if i == 0 else ""
        days_html += (
            f"<details class='day'{open_attr}>"
            f"<summary class='day-header'>"
            f"<div class='dh-left'>"
            f"<span class='day-num'>Day {i+1}</span>"
            f"<div class='dh-title'>"
            f"<span class='day-label'>{_safe(dlbl)}</span>"
            f"{('<span class=day-theme>' + _safe(theme) + '</span>') if theme else ''}"
            f"</div></div>"
            f"<div class='dh-right'>"
            f"{weather_html}"
            f"<span class='item-count'>{n_items} item{'s' if n_items != 1 else ''}</span>"
            f"<span class='chevron'>›</span>"
            f"</div>"
            f"</summary>"
            f"<div class='day-items'>{items_html or '<p class=empty-day>No items planned yet.</p>'}</div>"
            f"</details>"
        )

    # ── render ────────────────────────────────────────────────────────────────
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
    --radius:14px;
  }}
  html{{scroll-behavior:smooth}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
        background:var(--bg);color:var(--text);line-height:1.6;min-height:100vh}}

  /* ── hero ── */
  .hero{{background:linear-gradient(135deg,#0ea5e9 0%,#0d9488 55%,#7c3aed 100%);
         color:#fff;padding:52px 24px 62px;text-align:center;position:relative;overflow:hidden}}
  .hero::before{{content:'✈';position:absolute;font-size:280px;opacity:.06;top:50%;left:50%;
                 transform:translate(-50%,-50%) rotate(15deg);line-height:1;pointer-events:none}}
  .hero h1{{font-size:clamp(28px,5vw,48px);font-weight:900;letter-spacing:-1.5px;
            margin-bottom:8px;text-shadow:0 2px 16px rgba(0,0,0,.22)}}
  .hero .dates{{font-size:16px;opacity:.88;margin-bottom:6px;font-weight:500}}
  .hero .travelers{{font-size:13px;opacity:.7;font-weight:500}}
  .wave{{display:block;width:100%;height:50px;background:var(--bg);
         clip-path:ellipse(55% 100% at 50% 100%);margin-top:-1px}}

  /* ── stats bar ── */
  .stats-bar{{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;
              background:var(--surface);border-bottom:1px solid var(--border);
              padding:14px 20px}}
  .stat{{display:flex;flex-direction:column;align-items:center;gap:1px;
         min-width:68px;padding:4px 10px;border-radius:10px}}
  .stat:hover{{background:var(--bg)}}
  .sicon{{font-size:17px;line-height:1}}
  .stat strong{{font-size:15px;font-weight:800;color:var(--text)}}
  .stat span{{font-size:10px;color:var(--text-3);font-weight:700;
              text-transform:uppercase;letter-spacing:.06em}}

  /* ── page ── */
  .page{{max-width:800px;margin:0 auto;padding:28px 16px 60px}}

  /* ── toolbar ── */
  .toolbar{{display:flex;align-items:center;justify-content:space-between;
            flex-wrap:wrap;gap:10px;margin-bottom:24px}}
  .toolbar-title{{font-size:11px;font-weight:800;text-transform:uppercase;
                  letter-spacing:.1em;color:var(--text-3)}}
  .btn-row{{display:flex;gap:8px}}
  .btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;
        border-radius:9px;border:none;font-size:12px;font-weight:700;cursor:pointer;
        transition:opacity .12s}}
  .btn-print{{background:linear-gradient(135deg,var(--sky),var(--teal));color:#fff;
               box-shadow:0 3px 10px rgba(14,165,233,.3)}}
  .btn-print:hover{{opacity:.88}}
  .btn-expand{{background:var(--surface);color:var(--text-2);border:1.5px solid var(--border)}}
  .btn-expand:hover{{border-color:var(--sky);color:var(--sky-d)}}

  /* ── budget section ── */
  .card-section{{background:var(--surface);border:1.5px solid var(--border);
                 border-radius:var(--radius);padding:20px 22px;margin-bottom:20px;
                 box-shadow:0 2px 10px rgba(0,0,0,.05)}}
  .sec-title{{font-size:11px;font-weight:800;text-transform:uppercase;
              letter-spacing:.1em;color:var(--text-3);margin-bottom:14px;
              display:flex;align-items:center;gap:6px}}
  .bcards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}}
  .bcard{{background:var(--bg);border:1.5px solid var(--border);border-left:4px solid var(--c);
          border-radius:10px;padding:12px 12px 10px;position:relative}}
  .bicon{{font-size:20px;display:block;margin-bottom:6px;line-height:1}}
  .bcat{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;
         color:var(--text-3);display:block;margin-bottom:3px}}
  .bamt{{font-size:20px;font-weight:900;color:var(--c);display:block;margin-bottom:6px;
         letter-spacing:-.5px}}
  .bbar-bg{{height:4px;background:var(--border);border-radius:2px;margin-bottom:4px}}
  .bbar-fill{{height:4px;border-radius:2px;background:var(--c)}}
  .bpct{{font-size:10px;color:var(--text-3);font-weight:700}}
  .bper{{font-size:10px;font-weight:600;color:var(--c);opacity:.75;display:block;margin-bottom:4px}}
  .btotal{{margin-top:14px;font-size:14px;color:var(--text-2);font-weight:600;
           border-top:1px solid var(--border);padding-top:12px}}
  .btotal strong{{color:var(--text);font-size:16px}}

  /* ── accordion days ── */
  details.day{{background:var(--surface);border:1.5px solid var(--border);
               border-radius:var(--radius);margin-bottom:10px;
               box-shadow:0 2px 8px rgba(0,0,0,.04);overflow:hidden}}
  details.day[open]{{border-color:#bae6fd}}
  summary.day-header{{list-style:none;display:flex;align-items:center;
                       justify-content:space-between;padding:13px 16px;
                       cursor:pointer;gap:10px;
                       background:linear-gradient(90deg,var(--sky-l),var(--teal-l));
                       transition:background .15s;user-select:none}}
  summary.day-header::-webkit-details-marker{{display:none}}
  summary.day-header:hover{{background:linear-gradient(90deg,#bae6fd,#99f6e4)}}
  details[open] summary.day-header{{border-bottom:1px solid var(--border)}}
  .dh-left{{display:flex;align-items:center;gap:10px;flex:1;min-width:0}}
  .day-num{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.08em;
            color:#fff;background:var(--sky-d);border-radius:6px;padding:3px 7px;
            flex-shrink:0}}
  .dh-title{{display:flex;flex-direction:column;min-width:0}}
  .day-label{{font-size:14px;font-weight:800;color:var(--sky-d);
              white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .day-theme{{font-size:11px;color:var(--text-3);font-weight:600;margin-top:1px}}
  .dh-right{{display:flex;align-items:center;gap:8px;flex-shrink:0}}
  .wx{{font-size:11px;color:var(--text-3);background:#fff;border:1px solid var(--border);
       border-radius:20px;padding:3px 9px;font-weight:600;white-space:nowrap}}
  .item-count{{font-size:11px;color:var(--text-3);font-weight:700;
               background:#fff;border:1px solid var(--border);
               border-radius:20px;padding:3px 9px;white-space:nowrap}}
  .chevron{{font-size:18px;color:var(--sky-d);font-weight:700;
            transition:transform .2s;display:block;line-height:1}}
  details[open] .chevron{{transform:rotate(90deg)}}
  .day-items{{padding:12px;display:flex;flex-direction:column;gap:8px}}
  .empty-day{{font-size:13px;color:var(--text-3);text-align:center;padding:16px}}

  /* ── item cards ── */
  .icard{{display:flex;gap:10px;background:var(--ibg);
          border:1.5px solid var(--border);border-left:4px solid var(--ic);
          border-radius:10px;padding:10px 12px;align-items:flex-start}}
  .iicon{{font-size:18px;flex-shrink:0;margin-top:1px;line-height:1}}
  .idetails{{flex:1;min-width:0}}
  .itop{{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-bottom:2px}}
  .itime{{font-family:'SF Mono','Fira Code',monospace;font-size:11px;font-weight:700;
          color:var(--ic);background:#fff;border:1px solid var(--border);
          border-radius:5px;padding:1px 6px;flex-shrink:0;white-space:nowrap}}
  .ititle{{font-size:14px;font-weight:700;color:var(--text);flex:1;min-width:0}}
  .iprice{{font-size:12px;font-weight:800;color:var(--teal);background:var(--teal-l);
           border-radius:6px;padding:2px 8px;flex-shrink:0;white-space:nowrap}}
  .istatus{{font-size:10px;font-weight:700;border-radius:5px;padding:1px 7px;
            flex-shrink:0;text-transform:uppercase;letter-spacing:.05em}}
  .itags{{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}}
  .itag{{font-size:11px;color:var(--text-3);background:#fff;
         border:1px solid var(--border);border-radius:5px;padding:1px 7px;font-weight:600}}
  .inotes{{margin-top:5px;font-size:12px;color:var(--text-3);line-height:1.55}}

  /* ── footer ── */
  footer{{text-align:center;font-size:12px;color:var(--text-3);
          border-top:1px solid var(--border);padding:20px 16px 48px}}
  footer a{{color:var(--sky-d);text-decoration:none;font-weight:600}}

  /* ── print ── */
  @media print{{
    .toolbar,.stats-bar,.wave{{display:none}}
    .hero{{print-color-adjust:exact;-webkit-print-color-adjust:exact}}
    body{{background:#fff}}
    .day,.card-section{{box-shadow:none;border:1px solid #ddd}}
    details.day{{display:block}}
    details.day .day-items{{display:flex}}
    summary.day-header{{pointer-events:none}}
  }}
  @media(max-width:520px){{
    .hero{{padding:36px 16px 46px}}
    .bcards{{grid-template-columns:1fr 1fr}}
    .dh-right .item-count{{display:none}}
  }}
</style>
</head>
<body>

<div class="hero">
  <h1>✈ {_safe(dest)}</h1>
  {f'<div class="dates">{_safe(date_range)}</div>' if date_range else ''}
  {f'<div class="travelers">👤 {travelers} traveler{"s" if travelers != 1 else ""}</div>' if travelers else ''}
</div>
<div class="wave"></div>

<div class="stats-bar">{stats_html}</div>

<div class="page">

  <div class="toolbar">
    <span class="toolbar-title">📋 Trip Summary</span>
    <div class="btn-row">
      <button class="btn btn-expand" onclick="toggleAll()">⊞ Expand all</button>
      <button class="btn btn-print"  onclick="window.print()">🖨 Print / PDF</button>
    </div>
  </div>

  {''.join([
    '<div class="card-section">',
    '<div class="sec-title">💰 Budget Breakdown</div>',
    '<div class="bcards">', budget_cards_html, '</div>',
    f'<div class="btotal">Estimated total:&nbsp;<strong>${budget_total:,.0f}</strong>',
    f'{"&nbsp;·&nbsp;" + _safe(str(travelers)) + " traveler(s)" if travelers else ""}',
    '</div></div>',
  ]) if budget_cards_html else ''}

  <div class="toolbar" style="margin-top:4px">
    <span class="toolbar-title">📅 Day-by-Day Itinerary</span>
  </div>
  {days_html}

</div>

<footer>
  Shared via <a href="/">Travel Agent</a> &nbsp;·&nbsp; Read-only view
</footer>

<script>
  function toggleAll() {{
    const all = document.querySelectorAll('details.day');
    const anyOpen = [...all].some(d => d.open);
    all.forEach(d => d.open = !anyOpen);
    document.querySelector('.btn-expand').textContent = anyOpen ? '⊞ Expand all' : '⊟ Collapse all';
  }}
</script>
</body></html>""")


def _safe(s: object) -> str:
    """HTML-escape a value for safe inline insertion."""
    import html
    return html.escape(str(s)) if s else ""


# ---------------------------------------------------------------------------
# Public configuration — exposes non-secret keys to the frontend
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def api_config():
    """Return public config the frontend needs (Clerk publishable key, etc.)."""
    return JSONResponse({
        "clerk_publishable_key": os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        "auth_enabled": bool(os.getenv("CLERK_JWKS_URL", "").strip()),
    })


# ---------------------------------------------------------------------------
# User profile — register / retrieve authenticated user
# ---------------------------------------------------------------------------
@app.post("/api/me")
async def sync_user(request: Request):
    """Called on login to upsert user record from Clerk JWT claims."""
    auth_user = _user_from_request(request)
    if not auth_user or not auth_user["user_id"]:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = _user_store.upsert(
        auth_user["user_id"],
        email=auth_user.get("email", ""),
        name=auth_user.get("name", ""),
    )
    usage = _user_store.get_usage(user["id"])
    limits = _user_store.limits_for(user["plan"])
    return JSONResponse({"user": user, "usage": usage, "limits": limits})


@app.get("/api/me")
async def get_me(request: Request):
    """Return current user info + usage. Returns null user if anonymous."""
    auth_user = _user_from_request(request)
    if not auth_user or not auth_user["user_id"]:
        return JSONResponse({"user": None, "usage": None, "limits": None})
    user = _user_store.get(auth_user["user_id"])
    if not user:
        return JSONResponse({"user": None, "usage": None, "limits": None})
    usage = _user_store.get_usage(user["id"])
    limits = _user_store.limits_for(user["plan"])
    return JSONResponse({"user": user, "usage": usage, "limits": limits})


# ---------------------------------------------------------------------------
# Session management — server-generated IDs prevent client forgery
# ---------------------------------------------------------------------------
@app.post("/api/session/new")
async def new_session(request: Request):
    auth_user = _user_from_request(request)
    user_id = auth_user["user_id"] if auth_user else None
    # Ensure user record exists in DB for authenticated users
    if user_id:
        _user_store.upsert(user_id, email=auth_user.get("email", ""), name=auth_user.get("name", ""))
    session_id = secrets.token_urlsafe(32)
    _session_store.create(session_id, user_id=user_id)
    return JSONResponse({"session_id": session_id})


# ---------------------------------------------------------------------------
# Workspaces — collaborative trip planning
# ---------------------------------------------------------------------------
class WorkspaceCreate(BaseModel):
    name: str
    session_id: str | None = None


class WorkspaceInvite(BaseModel):
    email: str
    role: str = "editor"


@app.get("/api/workspaces")
async def list_workspaces(request: Request):
    """List all workspaces for the authenticated user."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return JSONResponse({"workspaces": _workspace_store.list_for_user(auth_user["user_id"])})


@app.post("/api/workspaces")
async def create_workspace(body: WorkspaceCreate, request: Request):
    """Create a new collaborative workspace."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    _user_store.upsert(auth_user["user_id"], email=auth_user.get("email", ""), name=auth_user.get("name", ""))
    ws = _workspace_store.create(body.name, auth_user["user_id"], session_id=body.session_id)
    return JSONResponse({"workspace": ws})


@app.get("/api/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, request: Request):
    """Get workspace details + members."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    ws = _workspace_store.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    role = _workspace_store.user_role(workspace_id, auth_user["user_id"])
    if not role:
        raise HTTPException(status_code=403, detail="Not a workspace member.")
    return JSONResponse({"workspace": ws, "my_role": role})


@app.post("/api/workspaces/{workspace_id}/invite")
async def invite_to_workspace(workspace_id: str, body: WorkspaceInvite, request: Request):
    """Invite a user by email to the workspace."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    ws = _workspace_store.get(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if ws["owner_id"] != auth_user["user_id"]:
        raise HTTPException(status_code=403, detail="Only the workspace owner can invite members.")
    member = _workspace_store.add_member(workspace_id, body.email, body.role)
    return JSONResponse({"status": "ok", "member": member})


@app.delete("/api/workspaces/{workspace_id}/members/{email}")
async def remove_workspace_member(workspace_id: str, email: str, request: Request):
    """Remove a member from the workspace."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    ok = _workspace_store.remove_member(workspace_id, email, auth_user["user_id"])
    if not ok:
        raise HTTPException(status_code=403, detail="Cannot remove this member.")
    return JSONResponse({"status": "ok"})


@app.post("/api/workspaces/{workspace_id}/session")
async def link_workspace_session(workspace_id: str, request: Request):
    """Link the current planning session to a workspace."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    body = await request.json()
    session_id = body.get("session_id", "")
    role = _workspace_store.user_role(workspace_id, auth_user["user_id"])
    if not role or role == "viewer":
        raise HTTPException(status_code=403, detail="Editor access required.")
    _workspace_store.link_session(workspace_id, session_id)
    return JSONResponse({"status": "ok"})


@app.delete("/api/workspaces/{workspace_id}")
async def delete_workspace(workspace_id: str, request: Request):
    """Delete a workspace (owner only)."""
    auth_user = _user_from_request(request)
    if not auth_user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    ok = _workspace_store.delete(workspace_id, auth_user["user_id"])
    if not ok:
        raise HTTPException(status_code=403, detail="Only the owner can delete this workspace.")
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Reset conversation (keeps trips and preferences)
# ---------------------------------------------------------------------------
@app.post("/api/reset/{session_id}")
async def reset(session_id: str, request: Request):
    auth_user = _user_from_request(request)
    _require_session_access(session_id, auth_user)
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
# Health check — component-level status without leaking secrets
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    import httpx as _hx
    components: dict[str, str] = {}

    # Database
    try:
        from memory.db import get_conn
        with get_conn() as conn:
            conn.cursor().execute("SELECT 1")
        components["database"] = "ok"
    except Exception:
        components["database"] = "error"

    # Anthropic API (lightweight — just check key is set, no real call)
    components["anthropic"] = "ok" if os.getenv("ANTHROPIC_API_KEY") else "unconfigured"

    # Optional services
    components["serpapi"]  = "configured" if os.getenv("SERPAPI_KEY")  else "not_configured"
    components["amadeus"]  = "configured" if os.getenv("AMADEUS_CLIENT_ID") else "not_configured"
    components["clerk"]    = "configured" if os.getenv("CLERK_JWKS_URL") else "not_configured"

    overall = "ok" if components["database"] == "ok" else "degraded"
    return JSONResponse({"status": overall, "components": components})


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
