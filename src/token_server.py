"""Token server and frontend host for Databricks Live Voice Kit.

This FastAPI app provides:
  - POST /api/token  — mints LiveKit room JWTs for browser clients
  - GET  /           — serves the voice UI (index.html)
  - GET  /health     — readiness probe for Databricks Apps

Runs alongside the LiveKit agent worker via main.py.
"""
from __future__ import annotations

import datetime
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from livekit.api import LiveKitAPI
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
from livekit.protocol.room import CreateRoomRequest
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from livekit.api import AccessToken, VideoGrants

logger = logging.getLogger("databricks-voice.token-server")

FRONTEND_DIR = Path(__file__).parent / "frontend"

app = FastAPI(
    title="Databricks Live Voice Kit",
    version="0.1.0",
    docs_url="/api/docs",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    """Request body for token generation."""
    # Room to join (auto-generated if empty)
    room: str = ""
    # Display name for the participant
    identity: str = ""


class TokenResponse(BaseModel):
    """Response with a signed LiveKit JWT."""
    token: str
    room: str
    identity: str
    livekit_url: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Readiness probe."""
    return {
        "status": "healthy",
        "service": "databricks-live-voice",
        "timestamp": time.time(),
    }


@app.post("/api/token", response_model=TokenResponse)
async def create_token(req: TokenRequest):
    """Mint a LiveKit room token for a browser client.

    The token grants permission to join the specified room, publish
    audio, and subscribe to the agent's audio and data streams.
    """
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    livekit_url = os.environ.get("LIVEKIT_URL", "")

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=503,
            detail="LiveKit credentials not configured. Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET.",
        )

    room = req.room or f"voice-{uuid.uuid4().hex[:8]}"
    identity = req.identity or f"user-{uuid.uuid4().hex[:6]}"

    # Mint a JWT with room join + audio publish grants
    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(datetime.timedelta(hours=1))
    )

    jwt = token.to_jwt()

    # Create the room and dispatch the agent to it.
    # Without explicit dispatch, LiveKit won't assign our registered
    # agent worker to the room — the user would join alone.
    try:
        lk_api = LiveKitAPI(
            url=livekit_url,
            api_key=api_key,
            api_secret=api_secret,
        )
        await lk_api.room.create_room(CreateRoomRequest(name=room))
        await lk_api.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                room=room,
                agent_name="databricks-voice",
            )
        )
        await lk_api.aclose()
        logger.info(f"Token minted + agent dispatched: room={room}, identity={identity}")
    except Exception as e:
        logger.warning(f"Agent dispatch failed (agent may not join): {e}")
        logger.info(f"Token minted (no dispatch): room={room}, identity={identity}")

    return TokenResponse(
        token=jwt,
        room=room,
        identity=identity,
        livekit_url=livekit_url,
    )


@app.get("/")
async def serve_frontend():
    """Serve the voice UI."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse(
        "<h1>Databricks Live Voice Kit</h1>"
        "<p>Frontend not found. Check src/frontend/index.html</p>",
        status_code=200,
    )


# Serve static assets from frontend/ if the directory exists
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
