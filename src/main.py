"""Databricks Live Voice Kit — unified entrypoint.

Runs two services in a single process:
  1. FastAPI token server + frontend (HTTP on DATABRICKS_APP_PORT or 8000)
  2. LiveKit agent worker (WebSocket to LiveKit Cloud)

The agent worker uses AgentServer.run() which is blocking, so it runs
in a background thread while the token server uses the main async loop.

Usage (always run from the project root with -m):
  python -m src.main              — production (Databricks App entrypoint)
  python -m src.agent dev         — agent only + LiveKit Playground
  python -m src.agent console     — text mode, no audio
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading

import uvicorn
from dotenv import load_dotenv

load_dotenv(".env.local")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
)
logger = logging.getLogger("databricks-voice.main")


def start_agent_worker() -> None:
    """Start the LiveKit agent worker in a background thread.

    AgentServer.run() is blocking — it manages its own event loop,
    connects to LiveKit Cloud, and dispatches rooms to our agent.
    """
    livekit_url = os.environ.get("LIVEKIT_URL", "")
    api_key = os.environ.get("LIVEKIT_API_KEY", "")
    api_secret = os.environ.get("LIVEKIT_API_SECRET", "")

    if not all([livekit_url, api_key, api_secret]):
        logger.warning(
            "LiveKit credentials not configured. "
            "Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET. "
            "Running in token-server-only mode (frontend will load but "
            "voice agent won't connect)."
        )
        return

    from src.agent import server as agent_server

    # Pass credentials explicitly so agent worker finds LiveKit
    agent_server.update_options(
        ws_url=livekit_url,
        api_key=api_key,
        api_secret=api_secret,
    )

    logger.info(f"Agent worker connecting to {livekit_url}")
    try:
        agent_server.run()
    except Exception:
        logger.exception("Agent worker crashed")


async def run_token_server(port: int) -> None:
    """Start the FastAPI server for token minting + frontend."""
    from src.token_server import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(config)
    logger.info(f"Token server starting on port {port}")
    await server.serve()


def main() -> None:
    """Run both services: agent worker in a thread, token server in main loop."""
    port = int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", "8000")))

    logger.info("=" * 60)
    logger.info("Databricks Live Voice Kit")
    logger.info(f"  Token server: http://0.0.0.0:{port}")
    logger.info(f"  Frontend:     http://0.0.0.0:{port}/")
    logger.info(f"  API docs:     http://0.0.0.0:{port}/api/docs")
    logger.info(f"  Health:       http://0.0.0.0:{port}/health")
    logger.info("=" * 60)

    # Start agent worker in background thread
    agent_thread = threading.Thread(
        target=start_agent_worker,
        name="agent-worker",
        daemon=True,
    )
    agent_thread.start()

    # Run token server in main thread (blocks)
    asyncio.run(run_token_server(port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
