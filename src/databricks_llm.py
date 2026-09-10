"""Databricks FMAPI LLM integration for LiveKit Agents.

Databricks Foundation Model APIs are OpenAI-compatible, so we use the
existing OpenAI plugin with a custom base_url. Zero custom plugin code.
"""
from __future__ import annotations

from livekit.agents import llm
from livekit.plugins.openai import LLM as OpenAILLM

from src.config import DatabricksConfig


def create_databricks_llm(
    model: str = "databricks-claude-sonnet-4-6",
    *,
    config: DatabricksConfig | None = None,
    temperature: float = 0.7,
    max_completion_tokens: int = 1024,
    parallel_tool_calls: bool = False,
) -> llm.LLM:
    """Create an LLM backed by Databricks FMAPI.

    Uses the OpenAI plugin with Databricks serving endpoint as base_url.
    No custom plugin needed — FMAPI is OpenAI-compatible.
    """
    if config is None:
        config = DatabricksConfig.resolve()

    return OpenAILLM(
        model=model,
        base_url=config.serving_base_url,
        api_key=config.token,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        parallel_tool_calls=parallel_tool_calls,
    )
