"""Databricks Live Voice Kit — configuration.

Resolves Databricks workspace credentials and voice profile settings.
Runs inside a Databricks App (auto-configured WorkspaceClient) or
locally with DATABRICKS_HOST / DATABRICKS_TOKEN env vars.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from databricks.sdk import WorkspaceClient


@dataclass(frozen=True)
class DatabricksConfig:
    """Resolved workspace connection details."""
    host: str
    token: str
    serving_base_url: str  # e.g. https://host/serving-endpoints

    @classmethod
    def resolve(cls) -> DatabricksConfig:
        """Auto-resolve from Databricks App context or env vars.

        Priority:
        1. Explicit env vars (DATABRICKS_HOST + DATABRICKS_TOKEN)
        2. WorkspaceClient auto-config (M2M OAuth in Apps, implicit in notebooks)

        In Databricks Apps, DATABRICKS_HOST is always set but DATABRICKS_TOKEN
        is not (the App uses M2M OAuth via client_id/client_secret).  LiveKit
        spawns child processes that inherit env vars, but the parent's
        os.environ mutations may not propagate reliably.  So we ALWAYS fall
        back to WorkspaceClient when the token is missing.
        """
        import logging
        log = logging.getLogger("databricks-voice.config")

        host = os.environ.get("DATABRICKS_HOST", "")
        token = os.environ.get("DATABRICKS_TOKEN", "")

        log.info(
            f"resolve(): DATABRICKS_HOST={'set' if host else 'missing'}, "
            f"DATABRICKS_TOKEN={'set' if token else 'missing'}"
        )

        # If we already have both, we're done.
        if host and token:
            return cls(
                host=host.rstrip("/"),
                token=token,
                serving_base_url=f"{host.rstrip('/')}/serving-endpoints",
            )

        # Otherwise, use WorkspaceClient to resolve what's missing.
        # In Databricks Apps this uses M2M OAuth (DATABRICKS_CLIENT_ID +
        # DATABRICKS_CLIENT_SECRET are auto-injected).
        try:
            w = WorkspaceClient()
            host = host or w.config.host.rstrip("/")

            if not token:
                # Try SDK's built-in token first (PAT-based auth)
                token = w.config.token or ""

            if not token:
                # M2M OAuth path: extract bearer token from auth headers
                auth_headers = w.config.authenticate()
                if callable(auth_headers):
                    auth_headers = auth_headers()
                token = auth_headers.get("Authorization", "").replace("Bearer ", "")
                log.info(f"resolve(): got token via authenticate() ({len(token)} chars)")

        except Exception as e:
            log.error(f"resolve(): WorkspaceClient failed: {e}")

        if not token:
            log.error("resolve(): NO TOKEN RESOLVED — LLM calls will fail!")

        # Ensure host always has https:// prefix (Databricks Apps may
        # set DATABRICKS_HOST without it).
        host = host.rstrip("/")
        if host and not host.startswith("http"):
            host = f"https://{host}"

        log.info(f"resolve(): final host={host}, token={'set' if token else 'MISSING'}")

        return cls(
            host=host,
            token=token,
            serving_base_url=f"{host}/serving-endpoints",
        )




# ---------------------------------------------------------------------------
# LiveKit credentials
# ---------------------------------------------------------------------------

def resolve_livekit_credentials(scope: str = "live-voice") -> tuple[str, str, str]:
    """Resolve (url, api_key, api_secret) for LiveKit.

    Priority:
      1. LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET env vars
      2. Databricks secret scope (default: `live-voice`) via the app's
         WorkspaceClient — zero-config on Databricks Apps; the app's
         service principal needs `secrets:read` on the scope.
    """
    import logging
    log = logging.getLogger("databricks-voice.config")

    url = os.environ.get("LIVEKIT_URL", "")
    key = os.environ.get("LIVEKIT_API_KEY", "")
    secret = os.environ.get("LIVEKIT_API_SECRET", "")
    if url and key and secret:
        return url, key, secret

    try:
        w = WorkspaceClient()
        url = url or w.dbutils.secrets.get(scope, "livekit-url")
        key = key or w.dbutils.secrets.get(scope, "livekit-api-key")
        secret = secret or w.dbutils.secrets.get(scope, "livekit-api-secret")
        log.info(f"resolve_livekit_credentials(): loaded from scope '{scope}'")
    except Exception as e:  # noqa: BLE001
        log.warning(f"resolve_livekit_credentials(): scope '{scope}' unavailable: {e}")
    return url, key, secret


VoiceProfile = Literal["premium", "balanced", "private", "hybrid"]


@dataclass
class VoiceConfig:
    """Voice agent configuration — maps to YAML profile system."""
    # LLM
    llm_model: str = "databricks-claude-sonnet-4-6"
    llm_temperature: float = 0.7
    llm_max_completion_tokens: int = 1024

    # STT (via LiveKit Inference for Phase 0; swap to self-hosted later)
    stt_model: str = "assemblyai/universal-3-5-pro"
    stt_language: str = "en"

    # TTS (via LiveKit Inference for Phase 0)
    tts_model: str = "fishaudio/s2.1-pro"
    tts_voice: str = "fa4c9eb3dccc4806b382b40d61c6b10a"  # Fish Audio default

    # Turn handling
    adaptive_interruption: bool = True
    preemptive_generation: bool = True
    expressive: bool = True

    # Databricks resources
    genie_space_id: str = ""
    warehouse_id: str = ""

    # Persona
    persona_style: str = "concise"
    max_spoken_turn_seconds: int = 25
    language: str = "en-US"

    @classmethod
    def from_profile(cls, profile: VoiceProfile) -> VoiceConfig:
        """Load a named profile."""
        profiles: dict[VoiceProfile, dict] = {
            "premium": {
                "llm_model": "databricks-claude-sonnet-4-6",
                "stt_model": "assemblyai/universal-3-5-pro",
                "tts_model": "fishaudio/s2.1-pro",
            },
            "balanced": {
                "llm_model": "databricks-gpt-4o-mini",
                "stt_model": "deepgram/nova-3",
                "tts_model": "cartesia/sonic-3",
            },
            "private": {
                "llm_model": "databricks-meta-llama-3-3-70b-instruct",
                "stt_model": "assemblyai/universal-3-5-pro",
                "tts_model": "fishaudio/s2.1-pro",
            },
            "hybrid": {
                "llm_model": "databricks-claude-sonnet-4-6",
                "stt_model": "deepgram/nova-3",
                "tts_model": "cartesia/sonic-3",
            },
        }
        return cls(**profiles.get(profile, {}))
