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
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.core import TravelAgent
from memory.preferences import PreferenceStore
from memory.trips import TripStore
from memory.sessions import SessionStore

app = FastAPI(title="Travel Agent API")

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

        # Booking confirmation for web: Claude manages the user-facing
        # confirmation dialogue through chat. We trust it and always allow.
        agent = TravelAgent(confirm_callback=lambda _msg: True)

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
async def chat(session_id: str, body: ChatRequest):
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
            # Persist after every successful reply
            _save_session(session_id, agent, latest_itinerary.get("value"))
            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "done", "content": response}),
                loop,
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "error", "message": str(e)}),
                loop,
            )

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


class PrefUpdate(BaseModel):
    key: str
    value: object


@app.post("/api/preferences/{session_id}")
async def set_preference(session_id: str, body: PrefUpdate):
    PreferenceStore().set(body.key, body.value)
    return JSONResponse({"status": "ok"})


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
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY")),
        "amadeus_set": bool(os.getenv("AMADEUS_CLIENT_ID")),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
