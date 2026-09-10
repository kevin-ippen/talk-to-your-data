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
        1. Explicit env vars (DATABRICKS_HOST, DATABRICKS_TOKEN)
        2. WorkspaceClient auto-config (Databricks Apps, notebooks)
        """
        host = os.environ.get("DATABRICKS_HOST", "")
        token = os.environ.get("DATABRICKS_TOKEN", "")

        if not host:
            w = WorkspaceClient()
            host = w.config.host.rstrip("/")
            # In Databricks Apps, token is available; in notebooks it may be
            # None (implicit auth). Fall back to generating a token.
            token = token or w.config.token or ""
            if not token:
                try:
                    # Generate a PAT-style token for API calls within the
                    # workspace (works in notebook context)
                    from databricks.sdk.service.iam import CreateTokenRequest
                    tok = w.tokens.create(
                        comment="databricks-live-voice-ephemeral",
                        lifetime_seconds=3600,
                    )
                    token = tok.token_value
                except Exception:
                    # Last resort: use the SDK's built-in auth header
                    auth_headers = w.config.authenticate()
                    if callable(auth_headers):
                        auth_headers = auth_headers()
                    token = auth_headers.get("Authorization", "").replace("Bearer ", "")

        return cls(
            host=host.rstrip("/"),
            token=token,
            serving_base_url=f"{host.rstrip('/')}/serving-endpoints",
        )


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
