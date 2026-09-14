# Databricks Live Voice Kit — Service Integration Guide

This guide covers using the Live Voice Kit as a **headless service** — no
browser frontend, no token server, no demo scaffold. Just the reusable
patterns for wiring LiveKit voice agents to Databricks.

## Architecture: What's Reusable vs. Demo Scaffold

```
talk-to-your-data/
├── src/
│   ├── config.py            ← REUSABLE: Databricks credential resolution
│   ├── databricks_llm.py    ← REUSABLE: FMAPI → LiveKit LLM adapter
│   ├── genie_tools.py       ← REUSABLE: Genie integration + narration engine
│   ├── agent.py             ← TEMPLATE: Voice agent (customize per use case)
│   ├── main.py              ← SCAFFOLD: Demo entrypoint (token server + agent)
│   ├── token_server.py      ← SCAFFOLD: FastAPI for browser JWT minting
│   └── frontend/            ← SCAFFOLD: Browser UI (index.html orb)
├── app.yaml                 ← SCAFFOLD: Databricks App config
└── requirements.txt         ← Both (prune for headless)
```

**To run as a pure service**, you need only `config.py`, `databricks_llm.py`,
`genie_tools.py`, and your own agent module. Everything else is demo chrome.

---

## Pattern 1: Minimal Headless Agent

A voice agent that connects to a LiveKit room, backed by Databricks FMAPI,
with no frontend, no token server. Use LiveKit's Agents Playground or any
RTC client to connect.

```python
# my_agent.py
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, inference
from livekit.plugins.openai import LLM as OpenAILLM

from src.config import DatabricksConfig

server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def session(ctx: JobContext):
    dbx = DatabricksConfig.resolve()

    llm = OpenAILLM(
        model="databricks-claude-sonnet-4-6",
        base_url=dbx.serving_base_url,
        api_key=dbx.token,
        temperature=0.7,
    )

    agent_session = AgentSession(
        stt=inference.STT(model="assemblyai/universal-3-5-pro"),
        llm=llm,
        tts=inference.TTS(model="fishaudio/s2.1-pro"),
    )

    agent = Agent(instructions="You are a helpful data analyst.")
    await agent_session.start(agent=agent, room=ctx.room)
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(server)
```

Run it:
```bash
export LIVEKIT_URL=wss://your-project.livekit.cloud
export LIVEKIT_API_KEY=...
export LIVEKIT_API_SECRET=...
export DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...

python my_agent.py dev   # connects to LiveKit, opens Agents Playground
python my_agent.py start # production mode
```

---

## Pattern 2: Adding Genie Tools

Drop-in Genie integration for any LiveKit agent. The `build_genie_tools()`
function returns standard `@function_tool`-decorated callables.

```python
from src.genie_tools import GenieMode, build_genie_tools
from src.config import DatabricksConfig

dbx = DatabricksConfig.resolve()

# Pick a mode:
#   GenieMode.AGENT — SSE streaming via Responses API (lowest latency)
#   GenieMode.CHAT  — Conversation polling API (widest compatibility)
#   GenieMode.MCP   — Genie One JSON-RPC (workspace-wide, multi-space)
tools = build_genie_tools(
    space_id="your-genie-space-id",
    host=dbx.host,
    token=dbx.token,
    mode=GenieMode.AGENT,
)

# Pass to any LiveKit Agent
agent = Agent(
    instructions="You are a franchise analyst.",
    tools=tools,
)
```

### Genie Mode Comparison

| Mode | API | Latency | Narration Source | Best For |
|------|-----|---------|-----------------|----------|
| AGENT | `POST /genie/agents/{id}/responses` (SSE) | Lowest | Real reasoning text from SSE events | Single-space, production |
| CHAT | `POST /genie/spaces/{id}/start-conversation` (poll) | Medium | Status transitions (FILTERING_CONTEXT, EXECUTING_QUERY) | Broad compatibility |
| MCP | `POST /mcp/genie` (JSON-RPC 2.0) | Medium | `<details>` reasoning blocks from poll | Multi-space, workspace-wide |

---

## Pattern 3: Narration Engine

The narration system speaks filler and real reasoning while Genie processes.
This is critical for voice UX — without it, the user hears silence for
10-30 seconds during query execution.

### How It Works

```
User asks question
    → _say(ctx, "Got it, checking on that now", cat="ack")
    → _emit_state(ctx, "understanding", "Understanding")
    → Genie SSE event: reasoning_summary_text.done
        → _clean_for_voice(reasoning_text)  # translate technical → natural
        → _say(ctx, cleaned_text, cat="reasoning", is_content=True)
    → Genie SSE event: function_call
        → _say(ctx, "Running the query now", cat="querying")
    → Genie SSE event: function_call_output
        → _say(ctx, "Results are in, let me summarize", cat="results")
    → 10s silence watchdog
        → _say(ctx, random filler from "waiting" pool)
```

### Key Design Decisions

1. **`add_to_chat_ctx=False`** on all `session.say()` calls. Without this,
   narration injects assistant messages into the LLM conversation, and
   FMAPI rejects the next turn with "does not support assistant message
   prefill" (400). The response never arrives.

2. **`ctx.session`** not `ctx.agent` — In livekit-agents 1.8+, `RunContext`
   exposes `.session` (property → `AgentSession`). There is no `.agent`
   attribute. Every `session.say()` call via `ctx.agent.session` silently
   fails with `AttributeError`.

3. **Two-tier pacing** — Real reasoning content (extracted from SSE/MCP
   events) uses a 3-second minimum gap. Generic filler uses an 8-second
   gap. This prevents rapid-fire filler while keeping real plan narration
   responsive.

4. **Phrase deduplication** — `_pick(category, state)` tracks used phrases
   per category and won't repeat until the pool is exhausted.

5. **Voice translation** — `_clean_for_voice()` converts technical reasoning
   to natural speech: strips SQL keywords (SELECT/FROM → "pulling data
   from"), removes fully qualified table names, replaces underscores with
   spaces, swaps analyst jargon ("aggregate" → "total", "partition by" →
   "broken down by"). Truncates to 2 sentences max.

### Customizing Narration

Replace the `NARRATION` dict in `genie_tools.py` with your domain phrases:

```python
NARRATION = {
    "ack": ["Let me look into that.", ...],
    "reasoning": ["Checking the sales data.", ...],
    "querying": ["Running the query.", ...],
    "results": ["Got the numbers back.", ...],
    "waiting": ["Still working on it.", ...],
    "routing": ["Finding the right data.", ...],
}
```

And update `_VOICE_REPLACEMENTS` for your domain:

```python
_VOICE_REPLACEMENTS = [
    (r"\byour_custom_table\b", "the customer data"),
    ...
]
```

---

## Pattern 4: Orb State via Data Channel

The agent publishes state updates to the frontend via LiveKit's data
channel. This is **optional** — the agent works fine without it. But if you
build a custom frontend, you get a 4-state orb for free.

### Agent → Frontend Messages

```json
{"type": "agent_state", "state": "understanding", "label": "Understanding"}
{"type": "agent_state", "state": "working",       "label": "Querying"}
{"type": "agent_state", "state": "working",       "label": "Analyzing"}
{"type": "agent_state", "state": "responding",    "label": "Responding"}
```

### State Machine

```
  Listening ──(user speaks)──→ Listening
      │                            │
      │ (user stops, 1.2s)         │ (user stops)
      ▼                            ▼
  Understanding ──(data channel)──→ Working
      │                              │ sub-labels:
      │                              │ Searching, Analyzing,
      │                              │ Querying, Calculating
      │                              ▼
      └──────────────────────── Responding
                                     │ (agent speaking)
                                     ▼
                                 Listening
```

### Frontend Listener (vanilla JS)

```javascript
room.on(RoomEvent.DataReceived, (payload, participant) => {
    if (participant?.isAgent) {
        const msg = JSON.parse(new TextDecoder().decode(payload));
        if (msg.type === 'agent_state') {
            setOrb(msg.state, msg.label);
        }
    }
});
```

---

## Pattern 5: Credential Resolution

`DatabricksConfig.resolve()` handles three environments transparently:

| Environment | How It Works |
|---|---|
| Databricks App | M2M OAuth via `WorkspaceClient().config.authenticate()`. `DATABRICKS_HOST` is set, token is extracted from auth headers. |
| Notebook | `WorkspaceClient()` auto-resolves from notebook context. |
| Local dev | Explicit `DATABRICKS_HOST` + `DATABRICKS_TOKEN` env vars. |

**Critical**: In tools that run during voice sessions, call
`DatabricksConfig.resolve()` via `asyncio.to_thread()` to avoid blocking
the event loop (which causes 400ms+ audio delays):

```python
from src.config import DatabricksConfig
dbx = await asyncio.to_thread(DatabricksConfig.resolve)
```

---

## Pattern 6: FMAPI as LLM Provider

Databricks Foundation Model APIs are OpenAI-compatible. Zero custom plugin
code — just point the OpenAI plugin at the serving endpoint:

```python
from livekit.plugins.openai import LLM as OpenAILLM
from src.config import DatabricksConfig

dbx = DatabricksConfig.resolve()

llm = OpenAILLM(
    model="databricks-claude-sonnet-4-6",
    base_url=dbx.serving_base_url,   # https://{host}/serving-endpoints
    api_key=dbx.token,
    temperature=0.7,
    max_completion_tokens=1024,
)
```

**Gotcha**: Do NOT pass `parallel_tool_calls=True` — FMAPI rejects it as
an unsupported parameter (400 Bad Request).

---

## Headless Deployment

For a pure-service deployment on Databricks Apps (no frontend):

### app.yaml (minimal)

```yaml
command:
  - python
  - -m
  - my_agent     # your agent module, not src.main
  - start

env:
  - name: LIVEKIT_URL
    value: "wss://your-project.livekit.cloud"
  - name: LIVEKIT_API_KEY
    value: "your-key"
  - name: LIVEKIT_API_SECRET
    value: "your-secret"
  - name: GENIE_SPACE_ID
    value: "your-space-id"
  - name: GENIE_MODE
    value: "agent"  # or chat, mcp
  - name: DATABRICKS_WAREHOUSE_ID
    valueFrom: sql-warehouse

resources:
  - name: sql-warehouse
    sql_warehouse:
      permission: CAN_USE
```

### requirements.txt (minimal)

```
livekit-agents>=1.6.9
livekit-plugins-openai>=1.0.0
livekit-api>=1.0.0
databricks-sdk>=0.40.0
python-dotenv
httpx
```

No `fastapi`, `uvicorn`, or `pyyaml` needed for headless.

---

## Quick Reference: LiveKit Agents SDK 1.8+

### RunContext API (inside @function_tool)

```python
ctx.session          # → AgentSession (THE way to access the session)
ctx.session.say()    # → SpeechHandle (direct TTS, no LLM)
ctx.update(msg)      # → push progress to LLM (it speaks a response)
ctx.with_filler()    # → auto-filler when idle (context manager)
ctx.speech_handle    # → current SpeechHandle
ctx.function_call    # → FunctionCall metadata
ctx.userdata         # → session userdata
```

### session.say() Gotchas

- `add_to_chat_ctx=False` — always, unless you want the spoken text in
  the LLM's conversation memory (you almost never do during tool execution)
- `allow_interruptions=True` — let the user cut in
- Returns `SpeechHandle`, not a coroutine (it's synchronous)

### Data Channel Publishing

```python
import asyncio, json
room = getattr(ctx.session, "_room", None)
if room:
    asyncio.ensure_future(
        room.local_participant.publish_data(
            json.dumps({"type": "my_event", ...}).encode(),
            reliable=True,
        )
    )
```
