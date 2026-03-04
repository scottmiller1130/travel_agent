"""
Centralised data directory resolution.

Checks TRAVEL_AGENT_DATA_DIR env var first so Railway volumes (or any
custom path) can be used. Falls back to ~/.travel_agent for local dev.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    env = os.getenv("TRAVEL_AGENT_DATA_DIR", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".travel_agent"
