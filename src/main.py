"""Databricks Live Voice Kit — unified entrypoint.

Runs two services in a single Databricks App container:
  1. LiveKit agent worker  (main thread — needs signal handlers + plugin lifecycle)
  2. FastAPI token server  (background thread — uvicorn is designed for embedding)

LiveKit AgentServer.run() MUST own the main thread: it registers plugins,
installs signal handlers, and manages the process event loop.  Uvicorn is
explicitly embeddable in background threads via its Server.run() API.

Usage (always run from the project root with -m):
  python -m src.main              — production (Databricks App entrypoint)
  python -m src.agent dev         — agent only + LiveKit Playground
  python -m src.agent console     — text mode, no audio
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback

import uvicorn
from dotenv import load_dotenv

load_dotenv(".env.local")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-5s %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("databricks-voice.main")


def start_token_server(port: int) -> None:
    """Run the FastAPI token server in a background thread.

    uvicorn.Server.run() is synchronous and creates its own event loop,
    so it works cleanly in a daemon thread.
    """
    try:
        from src.token_server import app

        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            access_log=True,
        )
        server = uvicorn.Server(config)
        logger.info(f"[http-thread] Token server starting on port {port}")
        server.run()
    except Exception:
        logger.error("[http-thread] Token server CRASHED:\n" + traceback.format_exc())


def main() -> None:
    """Run both services: token server in thread, agent worker on main thread."""
    port = int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", "8000")))

    # Resolve LiveKit creds (env first, then the `live-voice` secret scope)
    from src.config import resolve_livekit_credentials

    livekit_url, api_key, api_secret = resolve_livekit_credentials()
    # Seed env for the LiveKit Agents framework + token server, which read
    # LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET from the environment.
    os.environ.setdefault("LIVEKIT_URL", livekit_url)
    os.environ.setdefault("LIVEKIT_API_KEY", api_key)
    os.environ.setdefault("LIVEKIT_API_SECRET", api_secret)

    logger.info("=" * 60)
    logger.info("Databricks Live Voice Kit")
    logger.info(f"  Token server : http://0.0.0.0:{port}")
    logger.info(f"  LIVEKIT_URL  : {'set' if livekit_url else 'MISSING'}")
    logger.info(f"  API_KEY      : {'set' if api_key else 'MISSING'}")
    logger.info(f"  API_SECRET   : {'set' if api_secret else 'MISSING'}")
    logger.info(f"  Python       : {sys.version}")
    logger.info("=" * 60)

    # 1. Start token server in a background thread
    http_thread = threading.Thread(
        target=start_token_server,
        args=(port,),
        name="http-server",
        daemon=True,
    )
    http_thread.start()
    logger.info("Token server thread started")

    # 2. Run agent worker on the MAIN thread
    #    AgentServer.run() needs main thread for: plugin registration,
    #    signal handlers (SIGTERM/SIGINT), and process-level event loop.
    if not all([livekit_url, api_key, api_secret]):
        logger.warning(
            "LiveKit credentials not configured. "
            "Running in token-server-only mode."
        )
        # Keep main thread alive so the daemon HTTP thread doesn't die
        http_thread.join()
        return

    try:
        # Verify Databricks auth works in the main process (smoke test).
        # Do NOT cache the token in os.environ — M2M OAuth tokens expire
        # after ~1h. Each child process must resolve a fresh token via
        # WorkspaceClient().config.authenticate() on every job dispatch.
        logger.info("Verifying Databricks credentials (main process)...")
        from src.config import DatabricksConfig
        dbx = DatabricksConfig.resolve()
        logger.info(
            f"  host={dbx.host}  "
            f"token={'OK (' + str(len(dbx.token)) + ' chars)' if dbx.token else 'MISSING!'}"
        )

        logger.info("Importing agent module (main thread)...")
        from src.agent import server as agent_server
        logger.info("Agent module imported OK, plugins registered")

        agent_server.update_options(
            ws_url=livekit_url,
            api_key=api_key,
            api_secret=api_secret,
        )

        logger.info(f"Starting agent worker → {livekit_url}")
        import asyncio
        asyncio.run(agent_server.run())
    except KeyboardInterrupt:
        logger.info("Agent worker stopped (KeyboardInterrupt)")
    except SystemExit as e:
        logger.error(f"Agent worker called sys.exit({e.code})")
    except BaseException:
        logger.error("Agent worker CRASHED:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
