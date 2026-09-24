"""Genie integration for Databricks Live Voice Kit.

Three runtime modes: AGENT (SSE), CHAT (polling), MCP (Genie One).
Narration via ctx.session.say() + ctx.with_filler() for idle gaps.
Orb state updates via LiveKit data channel → frontend.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from enum import Enum

import httpx
from livekit.agents import RunContext, function_tool

logger = logging.getLogger("databricks-voice.genie")


class GenieMode(str, Enum):
    AGENT = "agent"
    CHAT  = "chat"
    MCP   = "mcp"


# -------------------------------------------------------------------
# Orb state machine: Listening → Understanding → Working → Responding
# Working has context-sensitive sub-labels.
# -------------------------------------------------------------------

def _emit_state(ctx: RunContext, state: str, label: str) -> None:
    """Send orb state to frontend via LiveKit data channel."""
    try:
        payload = json.dumps({"type": "agent_state", "state": state, "label": label})
        room = getattr(ctx.session, "_room", None)
        if room is None:
            room = getattr(ctx.session, "room", None)
        if room and hasattr(room, "local_participant"):
            # publish_data is a coroutine — schedule it without blocking
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(
                    room.local_participant.publish_data(payload.encode(), reliable=True)
                )
            logger.debug(f"[orb] {state}/{label}")
    except Exception as e:
        logger.debug(f"[orb] emit failed: {e}")


# -------------------------------------------------------------------
# Narration
# -------------------------------------------------------------------

NARRATION = {
    "ack": [
        "Got it, let me pull that up.",
        "Good question, checking on that now.",
        "On it, give me a sec.",
        "Sure, let me take a look.",
        "Yeah, let me dig into that.",
        "OK, pulling that together for you.",
        "Interesting one, let me see what we've got.",
        "Let me look into that real quick.",
        "Sure thing, one moment.",
        "Let me check on that.",
        "Yep, looking into it now.",
        "Great question, let me find out.",
        "OK let me see here.",
        "Alright, pulling that up.",
    ],
    "reasoning": [
        "Looking through the franchise data now.",
        "Let me see which tables have what we need.",
        "Checking the margin data across groups.",
        "Scanning the P and L trends for this.",
        "Cross-referencing a couple of data sources here.",
        "Narrowing down the relevant time periods.",
        "Figuring out the best way to slice this.",
        "Mapping this to the right franchise groups.",
        "Comparing against the risk score data too.",
        "Looking at both revenue and cost side of this.",
        "Let me check the store-level breakdowns.",
        "Pulling up the weekly trends on this.",
        "Seeing what the data says about that.",
        "Checking how the numbers break down by group.",
        "Let me look at the recent weeks first.",
        "Comparing a few different angles on this.",
    ],
    "querying": [
        "Running the query now.",
        "Pulling the numbers from the warehouse.",
        "Query is executing, should be quick.",
        "Query's in, waiting on the warehouse.",
        "Hitting the data warehouse for this.",
        "Executing against the live tables.",
        "Fetching the data now.",
        "Running that against the latest numbers.",
        "Query is in, pulling the rows.",
        "Sent the query off, give it a moment.",
    ],
    "results": [
        "OK, got the data back. Putting it together.",
        "Results are in, let me summarize.",
        "Alright, I can see the picture now.",
        "Numbers came back, let me walk you through it.",
        "Got a clear result here, one moment.",
        "Data's back, formatting the key takeaways.",
        "OK, the numbers are in. Let me read through this.",
        "Got the results, pulling out the highlights.",
        "Alright, data came through. Here's what I see.",
        "Numbers look good, let me put this together.",
    ],
    "waiting": [
        "Still working through this, bear with me.",
        "This one's a bit more involved, hang tight.",
        "Almost there, just finalizing.",
        "Still crunching, shouldn't be much longer.",
        "A few more seconds on this one.",
        "The query's still running, bigger dataset than usual.",
        "Getting close, just waiting on the last piece.",
        "Warehouse is still processing, one more moment.",
        "Still pulling the data together, almost done.",
        "Just a bit more, the numbers are coming in.",
        "Wrapping up the computation now.",
        "Lot of rows to go through here.",
        "Shouldn't be too much longer.",
        "Still running, give me another moment.",
        "Processing the results now.",
        "Hang on, just about done.",
        "Working through the last chunk of data.",
        "A little more time on this one.",
        "Going to need some deeper analysis on this one, give me a minute.",
        "This needs a deeper pass — hang on for a bit.",
        "Taking a bit longer than usual, the analysis is worth the wait.",
        "Digging a level deeper on this one, be right with you.",
    ],
    "deep": [
        "Going to need some deeper analysis on this one, give me a minute.",
        "This is turning into a real analysis project — give me a bit.",
        "Still deep in the data on this one. It's a meaty question.",
        "Taking the scenic route through the dataset, this one's layered.",
        "Deep dive in progress — this question has some depth to it.",
    ],
    "routing": [
        "Let me figure out the best data source for this.",
        "Searching across the franchise tables.",
        "Routing this to the right dataset.",
        "Checking which tables cover this question.",
        "Connecting to the data layer now.",
        "Finding the right data for that.",
        "Let me see where that lives.",
        "Matching your question to the right tables.",
    ],
}

# Maps narration category → orb state + label
NARRATION_TO_STATE = {
    "ack":       ("understanding", "Understanding"),
    "reasoning": ("working",       "Analyzing"),
    "querying":  ("working",       "Querying"),
    "results":   ("working",       "Calculating"),
    "waiting":   ("working",       "Working"),
    "deep":      ("working",       "Deep analysis"),
    "routing":   ("working",       "Searching"),
}

_MIN_FILLER_GAP = 16.0  # seconds between generic filler phrases
_MIN_CONTENT_GAP = 8.0  # seconds between real reasoning content


def _pick(category: str, state: dict) -> str:
    """Pick a phrase, avoiding recent repeats."""
    pool = NARRATION.get(category, ["One moment."])
    used = state.get("used", {})
    used_set = used.get(category, set())
    available = [p for p in pool if p not in used_set]
    if not available:
        used_set.clear()
        available = pool
    choice = random.choice(available)
    used_set.add(choice)
    used.setdefault(category, set())
    used[category] = used_set
    state["used"] = used
    return choice


def _say(ctx: RunContext, text: str, state: dict, category: str = "waiting",
         is_content: bool = False) -> None:
    """Speak narration via TTS and emit orb state.

    Args:
        is_content: True for actual reasoning text (shorter gap),
                    False for generic filler (longer gap).
    """
    now = time.monotonic()
    gap = now - state.get("last_say", 0)
    min_gap = _MIN_CONTENT_GAP if is_content else _MIN_FILLER_GAP
    if gap < min_gap:
        return

    # Emit orb state
    orb_state, orb_label = NARRATION_TO_STATE.get(category, ("working", "Working"))
    _emit_state(ctx, orb_state, orb_label)

    try:
        ctx.session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
        state["last_say"] = now
        logger.info(f"[narrate] SPOKE ({category}): '{text}'")
    except Exception as e:
        logger.error(f"[narrate] say() FAILED: {type(e).__name__}: {e}")


# Technical terms that sound awkward spoken aloud → natural replacements
_VOICE_REPLACEMENTS = [
    # SQL / DB jargon
    (r"\bSELECT\b.*?\bFROM\b", "pulling data from"),
    (r"\bGROUP BY\b", "grouped by"),
    (r"\bORDER BY\b", "sorted by"),
    (r"\bJOIN\b", "joining"),
    (r"\bWHERE\b", "filtering on"),
    (r"\bLIMIT \d+", ""),
    (r"\bCTE\b", "a subquery"),
    (r"\bUNION ALL\b", "combining"),
    # Table/column name patterns
    (r"\b\w+\.\w+\.\w+\b", ""),  # fully qualified table names
    (r"\b[a-z_]{2,}\.[a-z_]+\b", ""),  # schema.table references
    (r"_", " "),  # snake_case → spaces (after table names removed)
    # Analyst jargon
    (r"\bfiltering context\b", "narrowing down the relevant data"),
    (r"\bexecuting query\b", "running the query"),
    (r"\bpending warehouse\b", "waiting on the warehouse"),
    (r"\baggregate\b", "total"),
    (r"\baggregating\b", "totaling"),
    (r"\bpartition(ed)? by\b", "broken down by"),
    (r"\bwindow function\b", "a comparison across rows"),
]


def _clean_for_voice(text: str) -> str:
    """Translate reasoning/plan text into natural spoken narration."""
    # Strip code fences and inline code
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", "", text)
    # Strip markdown formatting
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Collapse whitespace
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()

    # Apply voice-friendly replacements
    for pattern, replacement in _VOICE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Clean up artifacts from replacements
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.])", r"\1", text)  # fix orphan punctuation
    text = text.strip().strip(",").strip()

    # Truncate to first 2 sentences — keep it concise for voice
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 2:
        text = " ".join(sentences[:2])

    # Prefix with a casual lead-in if it starts abruptly
    if text and not text[0].isupper() and not text.startswith(("I ", "I'")):
        text = text[0].upper() + text[1:]

    return text if len(text) > 15 else ""


def _strip_markdown(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)
    text = re.sub(r"\*\*Status:\*\*\s*\w+\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[Knowledge snippet\]\(#[^)]+\)", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        if re.match(r"^\|?\s*---", stripped):
            continue
        lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# -------------------------------------------------------------------
# Data structures
# -------------------------------------------------------------------

class GenieQueryError(Exception):
    pass

@dataclass
class GenieSession:
    space_id: str
    conversation_id: str | None = None
    mode: GenieMode = GenieMode.AGENT

@dataclass
class GenieResult:
    question: str
    answer: str
    sql: str = ""
    status: str = "completed"


# -------------------------------------------------------------------
# Agent mode -- SSE Responses API
# -------------------------------------------------------------------

async def _agent_mode_sse(
    ctx: RunContext,
    client: httpx.AsyncClient,
    host: str,
    headers: dict,
    agent_id: str,
    question: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 90.0,
) -> tuple[GenieResult, str | None]:
    url = f"{host}/api/2.0/genie/agents/{agent_id}/responses"
    body: dict = {
        "input": [{
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": question}],
        }],
    }
    if conversation_id:
        body["conversation_id"] = conversation_id

    conv_id = conversation_id
    final_text = ""
    sql = ""
    say_state: dict = {"last_say": 0}
    narrated = {"reasoning": False, "querying": False, "results": False}
    reasoning_buf = ""  # accumulates reasoning text across deltas

    try:
        async with client.stream(
            "POST", url, headers=headers, json=body,
            timeout=httpx.Timeout(timeout_seconds, connect=15.0),
        ) as response:
            if response.status_code != 200:
                err_body = await response.aread()
                raise httpx.HTTPStatusError(
                    f"{response.status_code}",
                    request=response.request,
                    response=httpx.Response(response.status_code, content=err_body),
                )

            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                raw = line[5:].lstrip()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = data.get("type", "")

                if etype == "response.created":
                    resp = data.get("response", {})
                    conv_id = resp.get("conversation_id", conv_id)
                    logger.info(f"[genie-sse] created: resp={resp.get('id', '?')[:12]}")

                elif etype == "response.output_item.added":
                    item = data.get("item", {})
                    itype = item.get("type", "")
                    if itype == "reasoning":
                        # Initial reasoning item — content usually empty here;
                        # real text arrives in done/delta events. Narrate start.
                        if not narrated["reasoning"]:
                            narrated["reasoning"] = True
                            _say(ctx, _pick("reasoning", say_state), say_state, "reasoning")
                    elif itype == "function_call":
                        fn_name = item.get("name", "")
                        logger.info(f"[genie-sse] function_call: {fn_name}")
                        if not narrated["querying"]:
                            narrated["querying"] = True
                            _say(ctx, _pick("querying", say_state), say_state, "querying")
                    elif itype == "function_call_output":
                        if not narrated["results"]:
                            narrated["results"] = True
                            _say(ctx, _pick("results", say_state), say_state, "results")

                # Reasoning summary deltas — accumulate and speak chunks
                elif etype == "response.reasoning_summary_text.delta":
                    reasoning_buf += data.get("delta", "")
                elif etype == "response.reasoning_summary_text.done":
                    summary = data.get("text", reasoning_buf).strip()
                    if summary and len(summary) > 15:
                        # Clean for voice and speak the actual plan
                        clean = _clean_for_voice(summary)
                        if clean:
                            _say(ctx, clean, say_state, "reasoning", is_content=True)
                            logger.info(f"[genie-sse] reasoning summary: {clean[:100]}")
                    reasoning_buf = ""

                elif etype == "response.output_text.done":
                    final_text += data.get("text", "")

                elif etype == "response.output_item.done":
                    item = data.get("item", {})
                    itype = item.get("type", "")
                    if itype == "reasoning":
                        # Final reasoning item — extract and speak content
                        for c in item.get("content", []):
                            ctype = c.get("type", "")
                            ctext = c.get("text", "").strip()
                            if ctype in ("reasoning_text", "summary_text",
                                         "text") and ctext and len(ctext) > 15:
                                clean = _clean_for_voice(ctext)
                                if clean:
                                    _say(ctx, clean, say_state, "reasoning",
                                         is_content=True)
                                    logger.info(
                                        f"[genie-sse] reasoning done: {clean[:100]}")
                                break
                    elif itype == "message":
                        for c in item.get("content", []):
                            if c.get("type") == "output_text":
                                final_text += c.get("text", "")
                    elif itype == "function_call":
                        try:
                            args = json.loads(item.get("arguments", "{}"))
                            sql = args.get("query", sql)
                            title = args.get("title", "")
                            if title:
                                _say(ctx, f"Running a query: {title}",
                                     say_state, "querying", is_content=True)
                        except (json.JSONDecodeError, TypeError):
                            pass

                elif etype == "response.completed":
                    resp = data.get("response", {})
                    for out in resp.get("output", []):
                        if out.get("type") == "message" and not final_text:
                            for c in out.get("content", []):
                                if c.get("type") == "output_text":
                                    final_text += c.get("text", "")
                    break

                elif etype == "response.failed":
                    err = data.get("response", {}).get("error", {})
                    return GenieResult(
                        question=question,
                        answer=f"I ran into an issue: {err.get('message', 'query failed')}",
                        status="failed",
                    ), conv_id

                # Silence watchdog
                now = time.monotonic()
                if now - say_state.get("last_say", 0) > 10.0:
                    _say(ctx, _pick("waiting", say_state), say_state, "waiting")

    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        body_text = ""
        try:
            body_text = e.response.text[:300]
        except Exception:
            pass
        logger.error(f"[genie-sse] HTTP {code}: {body_text}")
        if code == 403:
            return GenieResult(question=question, answer="I don't have permission to access that data.", status="failed"), conv_id
        if code == 404:
            return GenieResult(question=question, answer="__FALLBACK_TO_CHAT__", status="fallback"), conv_id
        if code == 409:
            return GenieResult(question=question, answer="There's already a query running. One moment.", status="failed"), conv_id
        return GenieResult(question=question, answer=f"Had trouble connecting. (HTTP {code})", status="failed"), conv_id
    except httpx.TimeoutException:
        return GenieResult(question=question, answer="That analysis timed out. Try a more specific question?", status="timeout"), conv_id

    answer = _strip_markdown(final_text) if final_text else "I got a result but couldn't extract a clear summary."
    return GenieResult(question=question, answer=answer, sql=sql, status="completed"), conv_id


# -------------------------------------------------------------------
# Chat mode -- Conversation polling API
# -------------------------------------------------------------------

CHAT_NARRATION = {
    "FILTERING_CONTEXT": "reasoning",
    "ASKING_AI":         "reasoning",
    "PENDING_WAREHOUSE": "waiting",
    "EXECUTING_QUERY":   "querying",
}

async def _chat_mode_poll(
    ctx: RunContext,
    client: httpx.AsyncClient,
    host: str,
    headers: dict,
    space_id: str,
    question: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 90.0,
) -> tuple[GenieResult, str | None]:
    base = f"{host}/api/2.0/genie/spaces/{space_id}"
    say_state: dict = {"last_say": 0}

    if conversation_id:
        resp = await client.post(
            f"{base}/conversations/{conversation_id}/messages",
            headers=headers, json={"content": question},
        )
    else:
        resp = await client.post(
            f"{base}/start-conversation",
            headers=headers, json={"content": question},
        )
    resp.raise_for_status()
    data = resp.json()
    conv_id = data.get("conversation_id", conversation_id)
    msg_id = data.get("message_id")

    deadline = time.monotonic() + timeout_seconds
    last_status = ""

    while time.monotonic() < deadline:
        await asyncio.sleep(2.0)
        poll = await client.get(
            f"{base}/conversations/{conv_id}/messages/{msg_id}",
            headers=headers,
        )
        poll.raise_for_status()
        msg = poll.json()
        status = msg.get("status", "UNKNOWN")

        if status != last_status:
            elapsed = int(timeout_seconds - (deadline - time.monotonic()))
            logger.info(f"[genie-chat] {status} [{elapsed}s]")
            narr_cat = CHAT_NARRATION.get(status)
            if narr_cat:
                _say(ctx, _pick(narr_cat, say_state), say_state, narr_cat)
            last_status = status

        if status == "COMPLETED":
            return _extract_chat_result(question, msg), conv_id
        if status in ("FAILED", "QUERY_RESULT_EXPIRED"):
            err = msg.get("error", {}).get("message", "Query failed.")
            return GenieResult(question=question, answer=f"Issue: {err}", status="failed"), conv_id
        if status == "CANCELLED":
            return GenieResult(question=question, answer="Query was cancelled.", status="failed"), conv_id

        wait_cat = "deep" if elapsed > 45 else "waiting"
        if time.monotonic() - say_state.get("last_say", 0) > 10.0:
            _say(ctx, _pick(wait_cat, say_state), say_state, wait_cat)

    return GenieResult(question=question, answer="Timed out. Try a more specific question?", status="timeout"), conv_id


def _extract_chat_result(question: str, msg: dict) -> GenieResult:
    content = msg.get("content", "")
    sql = ""
    text_parts: list[str] = []
    for att in msg.get("attachments", []):
        qi = att.get("query", {})
        if qi.get("query"):
            sql = qi["query"]
        text_att = att.get("text", {})
        if text_att.get("content"):
            text_parts.append(text_att["content"])
    answer = "\n".join(text_parts) if text_parts else content
    return GenieResult(question=question, answer=answer, sql=sql, status="completed")


# -------------------------------------------------------------------
# MCP mode -- Genie One
# -------------------------------------------------------------------

async def _mcp_mode(
    ctx: RunContext,
    client: httpx.AsyncClient,
    host: str,
    headers: dict,
    question: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 90.0,
) -> tuple[GenieResult, str | None]:
    mcp_url = f"{host}/ai-gateway/mcp-services/system.ai.genie_one_mcp"
    say_state: dict = {"last_say": 0}

    ask_args: dict = {"question": question}
    if conversation_id:
        ask_args["conversation_id"] = conversation_id

    resp = await client.post(mcp_url, headers=headers, json={
        "jsonrpc": "2.0", "id": "ask",
        "method": "tools/call",
        "params": {"name": "genie_ask", "arguments": ask_args},
    })
    resp.raise_for_status()
    ask_text = _mcp_text(resp.json().get("result", {}))

    try:
        ask_data = json.loads(ask_text)
    except (json.JSONDecodeError, TypeError):
        return GenieResult(question=question, answer="Had trouble starting the query.", status="failed"), conversation_id

    conv_id = ask_data.get("conversation_id", conversation_id)
    resp_id = ask_data.get("response_id")
    logger.info(f"[genie-mcp] ask: conv={conv_id}, resp={resp_id}")

    _say(ctx, _pick("routing", say_state), say_state, "routing")

    deadline = time.monotonic() + timeout_seconds
    prev_reasoning_count = 0

    while time.monotonic() < deadline:
        await asyncio.sleep(3.0)

        poll_resp = await client.post(mcp_url, headers=headers, json={
            "jsonrpc": "2.0", "id": "poll",
            "method": "tools/call",
            "params": {
                "name": "genie_poll_response",
                "arguments": {"conversation_id": conv_id, "response_id": resp_id},
            },
        })
        poll_resp.raise_for_status()
        poll_text = _mcp_text(poll_resp.json().get("result", {}))

        status_match = re.search(r"\*\*Status:\*\*\s*(\w+)", poll_text)
        status = status_match.group(1).lower() if status_match else "unknown"

        reasoning_blocks = re.findall(
            r"<details><summary>Genie's reasoning</summary>(.*?)</details>",
            poll_text, re.DOTALL,
        )
        if len(reasoning_blocks) > prev_reasoning_count:
            blk_raw = reasoning_blocks[-1].strip()
            # Try to speak the actual reasoning content
            clean = _clean_for_voice(blk_raw)
            if clean:
                _say(ctx, clean, say_state, "reasoning", is_content=True)
                logger.info(f"[genie-mcp] reasoning: {clean[:100]}")
            else:
                # Fall back to category-matched filler
                blk_lower = blk_raw.lower()
                if "search" in blk_lower:
                    _say(ctx, _pick("routing", say_state), say_state, "routing")
                elif "query" in blk_lower or "sql" in blk_lower:
                    _say(ctx, _pick("querying", say_state), say_state, "querying")
                elif "returned" in blk_lower or "answer" in blk_lower:
                    _say(ctx, _pick("results", say_state), say_state, "results")
                else:
                    _say(ctx, _pick("reasoning", say_state), say_state, "reasoning")
            prev_reasoning_count = len(reasoning_blocks)

        elapsed = int(time.monotonic() - (deadline - timeout_seconds))
        logger.info(f"[genie-mcp] poll: status={status} [{elapsed}s]")

        if status == "completed":
            return GenieResult(question=question, answer=_strip_markdown(poll_text), status="completed"), conv_id
        if status in ("failed", "incomplete"):
            answer = _strip_markdown(poll_text) or "Query didn't return a result. Try rephrasing?"
            return GenieResult(question=question, answer=answer, status="failed"), conv_id

        if time.monotonic() - say_state.get("last_say", 0) > 10.0:
            _say(ctx, _pick("waiting", say_state), say_state, "waiting")

    return GenieResult(question=question, answer="Timed out. Try a more specific question?", status="timeout"), conv_id


def _mcp_text(result: dict) -> str:
    for c in result.get("content", []):
        if c.get("type") == "text":
            return c["text"]
    return ""


# -------------------------------------------------------------------
# Voice formatting
# -------------------------------------------------------------------

def _format_for_voice(result: GenieResult) -> str:
    if result.status != "completed":
        return result.answer
    if result.answer:
        clean = _strip_markdown(result.answer)
        return clean if clean else "The query ran but I couldn't extract a clear summary."
    return "The query ran but I couldn't extract a clear summary."


# -------------------------------------------------------------------
# Tool builder
# -------------------------------------------------------------------

def build_genie_tools(
    space_id: str,
    host: str,
    token: str,
    mode: GenieMode = GenieMode.AGENT,
) -> list:
    session = GenieSession(space_id=space_id, mode=mode)

    @function_tool
    async def query_data(
        ctx: RunContext,
        question: str,
    ) -> str:
        """Query franchise operations data using natural language.

        Ask about store performance, franchise group margins, P&L trends,
        risk scores, labor costs, food costs, revenue, or any business KPI.
        Results come from governed SQL against Unity Catalog tables.

        Args:
            question: The analytical question (e.g. "Which franchise groups
                      had the highest margin improvement this quarter?")
        """
        say_state: dict = {"last_say": 0}
        logger.info(
            f"[genie] mode={session.mode.value} question='{question}' "
            f"conv={session.conversation_id or 'new'}"
        )

        # Immediate acknowledgment + orb state
        _say(ctx, _pick("ack", say_state), say_state, "ack")

        # Resolve fresh token in background thread (sync HTTP blocks event loop)
        from src.config import DatabricksConfig
        dbx = await asyncio.to_thread(DatabricksConfig.resolve)
        req_headers = {
            "Authorization": f"Bearer {dbx.token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                if session.mode == GenieMode.AGENT:
                    result, conv_id = await _agent_mode_sse(
                        ctx=ctx, client=client,
                        host=dbx.host, headers=req_headers,
                        agent_id=space_id, question=question,
                        conversation_id=session.conversation_id,
                        timeout_seconds=90.0,
                    )
                    if result.status == "fallback":
                        logger.info("[genie] agent 404, falling back to chat")
                        result, conv_id = await _chat_mode_poll(
                            ctx=ctx, client=client,
                            host=dbx.host, headers=req_headers,
                            space_id=space_id, question=question,
                            conversation_id=session.conversation_id,
                            timeout_seconds=90.0,
                        )

                elif session.mode == GenieMode.CHAT:
                    result, conv_id = await _chat_mode_poll(
                        ctx=ctx, client=client,
                        host=dbx.host, headers=req_headers,
                        space_id=space_id, question=question,
                        conversation_id=session.conversation_id,
                        timeout_seconds=90.0,
                    )

                elif session.mode == GenieMode.MCP:
                    result, conv_id = await _mcp_mode(
                        ctx=ctx, client=client,
                        host=dbx.host, headers=req_headers,
                        question=question,
                        conversation_id=session.conversation_id,
                        timeout_seconds=90.0,
                    )

                else:
                    result = GenieResult(question=question, answer="Unknown mode.", status="failed")
                    conv_id = session.conversation_id

            session.conversation_id = conv_id
            logger.info(
                f"[genie] {result.status}: "
                f"answer={result.answer[:150] if result.answer else 'NONE'}"
            )

            # Signal responding state
            _emit_state(ctx, "responding", "Responding")

            return _format_for_voice(result)

        except Exception as e:
            logger.error(f"[genie] unexpected: {e}", exc_info=True)
            return "Something went wrong querying the data. Want to try again?"

    return [query_data]
