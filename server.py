#!/usr/bin/env python3
"""
FastAPI web server for the Travel Agent.

Endpoints:
  GET  /                          → serve the chat UI
  POST /api/chat/{session_id}     → SSE stream: tool events + final response
  GET  /api/trips/{session_id}    → saved trips JSON
  GET  /api/preferences/{sid}     → user preferences JSON
  POST /api/preferences/{sid}     → update a preference
  POST /api/reset/{session_id}    → clear conversation history

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

app = FastAPI(title="Travel Agent API")

# ---------------------------------------------------------------------------
# Session registry  {session_id: {"agent": TravelAgent, "prefs": ..., "trips": ...}}
# ---------------------------------------------------------------------------
SESSIONS: dict[str, dict] = {}


def get_or_create_session(session_id: str) -> dict:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {
            "agent": TravelAgent(),
            "prefs": PreferenceStore(),
            "trips": TripStore(),
            "itinerary": None,
        }
    return SESSIONS[session_id]


# ---------------------------------------------------------------------------
# Static files (the frontend HTML lives at static/index.html)
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def root():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>Frontend not found</h1><p>static/index.html is missing.</p>", status_code=404)
    return HTMLResponse(index.read_text())


# ---------------------------------------------------------------------------
# Chat endpoint — SSE stream
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat/{session_id}")
async def chat(session_id: str, body: ChatRequest):
    session = get_or_create_session(session_id)
    agent: TravelAgent = session["agent"]

    # asyncio.Queue lets the background thread push events to the async generator
    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue = asyncio.Queue()

    def progress_callback(event_type: str, data: dict):
        """Called from the agent thread; pushes SSE events to the async queue."""
        if event_type == "itinerary_update":
            session["itinerary"] = data.get("itinerary")
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

    # Run the (blocking) agent in a thread so we don't block the event loop
    threading.Thread(target=run_agent, daemon=True).start()

    async def event_stream():
        # Send an immediate heartbeat so Railway/proxies don't buffer the response
        yield ": connected\n\n"
        while True:
            try:
                # Wait up to 15s; if nothing arrives, send a heartbeat to keep the connection alive
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
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if behind proxy
        },
    )


# ---------------------------------------------------------------------------
# Itinerary (visual board state)
# ---------------------------------------------------------------------------
@app.get("/api/itinerary/{session_id}")
async def get_itinerary(session_id: str):
    session = get_or_create_session(session_id)
    return JSONResponse({"itinerary": session.get("itinerary")})


# ---------------------------------------------------------------------------
# Trips
# ---------------------------------------------------------------------------
@app.get("/api/trips/{session_id}")
async def get_trips(session_id: str):
    session = get_or_create_session(session_id)
    trips = session["trips"].get_all_trips()
    return JSONResponse({"trips": trips})


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
@app.get("/api/preferences/{session_id}")
async def get_preferences(session_id: str):
    session = get_or_create_session(session_id)
    return JSONResponse(session["prefs"].get_all())


class PrefUpdate(BaseModel):
    key: str
    value: object


@app.post("/api/preferences/{session_id}")
async def set_preference(session_id: str, body: PrefUpdate):
    session = get_or_create_session(session_id)
    session["prefs"].set(body.key, body.value)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Reset conversation
# ---------------------------------------------------------------------------
@app.post("/api/reset/{session_id}")
async def reset(session_id: str):
    session = get_or_create_session(session_id)
    session["agent"].reset()
    return JSONResponse({"status": "ok", "message": "Conversation reset."})


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"status": "ok", "api_key_set": bool(os.getenv("ANTHROPIC_API_KEY"))}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
