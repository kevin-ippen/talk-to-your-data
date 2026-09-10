"""Genie space integration as LiveKit Agent tools.

Provides function_tool wrappers that let a voice agent query
Databricks Genie spaces for grounded, governed SQL analytics.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx

from livekit.agents import RunContext, ToolError, function_tool

logger = logging.getLogger("databricks-voice.genie")

GENIE_API_BASE = "/api/2.0/genie/spaces"


@dataclass
class GenieResult:
    """Result from a Genie space query."""
    question: str
    sql: str
    summary: str
    columns: list[str]
    rows: list[list]
    row_count: int


async def query_genie_space(
    host: str,
    token: str,
    space_id: str,
    question: str,
    *,
    timeout_seconds: float = 60.0,
    poll_interval: float = 2.0,
) -> GenieResult:
    """Query a Genie space and wait for the result.

    Uses the Genie Conversation API: start a conversation, poll until
    the query completes, then extract the result.
    """
    base = f"{host.rstrip('/')}{GENIE_API_BASE}/{space_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        # Start conversation
        start_resp = await client.post(
            f"{base}/start-conversation",
            headers=headers,
            json={"content": question},
        )
        start_resp.raise_for_status()
        start_data = start_resp.json()

        conversation_id = start_data["conversation_id"]
        message_id = start_data["message_id"]

        # Poll for completion
        deadline = time.monotonic() + timeout_seconds
        result_data = None

        while time.monotonic() < deadline:
            poll_resp = await client.get(
                f"{base}/conversations/{conversation_id}/messages/{message_id}",
                headers=headers,
            )
            poll_resp.raise_for_status()
            msg = poll_resp.json()

            status = msg.get("status", "")
            if status == "COMPLETED":
                result_data = msg
                break
            elif status in ("FAILED", "CANCELLED"):
                error_msg = msg.get("error", {}).get("message", "Query failed")
                raise ToolError(f"Genie query failed: {error_msg}")

            await _async_sleep(poll_interval)

        if result_data is None:
            raise ToolError(f"Genie query timed out after {timeout_seconds}s")

        # Extract result from attachments
        attachments = result_data.get("attachments", [])
        query_attachment = next(
            (a for a in attachments if a.get("query", {}).get("query")), None
        )

        sql = ""
        summary = result_data.get("content", "")
        columns = []
        rows = []

        if query_attachment:
            query_info = query_attachment["query"]
            sql = query_info.get("query", "")
            result_info = query_info.get("query_result", {})
            columns = [c.get("name", "") for c in result_info.get("columns", [])]
            rows = result_info.get("data_array", [])

        return GenieResult(
            question=question,
            sql=sql,
            summary=summary,
            columns=columns,
            rows=rows,
            row_count=len(rows),
        )


async def _async_sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


def build_genie_tool(space_id: str, host: str, token: str):
    """Build a function_tool that queries a specific Genie space."""

    @function_tool
    async def query_data(
        ctx: RunContext,
        question: str,
    ) -> str:
        """Query enterprise data using natural language.

        Ask questions about business metrics, trends, comparisons, and KPIs.
        The system executes governed SQL against Unity Catalog tables and
        returns grounded results — never hallucinated data.

        Args:
            question: The analytical question to answer (e.g. "What were our
                      top 5 stores by revenue last quarter?")
        """
        logger.info(f"Genie query: {question}")
        result = await query_genie_space(
            host=host,
            token=token,
            space_id=space_id,
            question=question,
        )

        # Format for voice: concise summary + key data points
        if result.row_count == 0:
            return f"The query ran successfully but returned no rows. SQL: {result.sql}"

        # Build a readable summary for TTS
        lines = [result.summary] if result.summary else []
        if result.row_count <= 10:
            header = " | ".join(result.columns)
            lines.append(f"Columns: {header}")
            for row in result.rows[:5]:
                lines.append(" | ".join(str(v) for v in row))
            if result.row_count > 5:
                lines.append(f"... and {result.row_count - 5} more rows")
        else:
            lines.append(f"Query returned {result.row_count} rows.")
            lines.append(f"First row: {' | '.join(str(v) for v in result.rows[0])}")

        return "\n".join(lines)

    return query_data
