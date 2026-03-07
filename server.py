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
# Startup validation — fail fast if required environment variables are missing
# ---------------------------------------------------------------------------
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not _ANTHROPIC_API_KEY:
    print(
        "FATAL: ANTHROPIC_API_KEY is not set. "
        "Add it to your environment or .env file before starting the server.",
        file=sys.stderr,
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
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
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
RATE_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> bool:
    """Return True if allowed, False if rate limit exceeded."""
    now = time.monotonic()
    with _rate_lock:
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
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "amadeus_set": bool(os.getenv("AMADEUS_CLIENT_ID")),
    }


# ---------------------------------------------------------------------------
# Automated DB backup — copies sessions.db daily, keeps last 7 snapshots
# ---------------------------------------------------------------------------
def _backup_db() -> None:
    """Copy sessions.db to a timestamped backup. Runs in a background thread."""
    from memory.sessions import DB_PATH
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    while True:
        time.sleep(86400)  # sleep 24 hours
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
            print(f"[backup] DB backup failed: {exc}", file=sys.stderr)


threading.Thread(target=_backup_db, daemon=True, name="db-backup").start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
