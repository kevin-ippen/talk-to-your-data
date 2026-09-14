# Databricks Live Voice Kit

A live voice interface for Databricks. Ask questions about your data by speaking
and get grounded answers from Genie spaces via natural conversation. Runs as a
Databricks App or standalone service.

## Architecture

```
Browser / RTC Client           LiveKit Cloud             Databricks App
──────────────────────     ─────────────────     ────────────────────────────────
                             WebRTC audio               ┌────────────────────────────┐
  [User speaks] ────WebRTC────▶ [LiveKit SFU] ──WS──▶ │ Agent Worker             │
  [User hears ] ◀───WebRTC──── [LiveKit SFU] ◀──WS── │   STT → LLM → TTS         │
                                               │   Genie tools + narration │
  Data channel: orb state ◀──────────────────│   Data channel → frontend │
                                               └───────┬────────────────────┘
                                                       │
                                               ┌───────┴────────────────────┐
                                               │ Databricks Platform       │
                                               │   FMAPI (Claude/GPT)      │
                                               │   Genie Spaces (3 modes)  │
                                               │   Unity Catalog            │
                                               └────────────────────────────┘
```

## Quick Start

### Prerequisites

1. **LiveKit Cloud** (free tier): https://cloud.livekit.io/
   Create a project → get `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

2. **Databricks workspace** with FMAPI access and (optionally) a Genie space

3. **Python 3.10+**

### Local Development

```bash
cp .env.example .env.local
# Edit with your LiveKit + Databricks credentials
pip install -r requirements.txt

# Full demo (frontend + agent)
python -m src.main              # → http://localhost:8000

# Headless (agent only, use LiveKit Agents Playground)
python -m src.agent dev         # → https://agents-playground.livekit.io

# Text mode (no audio, terminal)
python -m src.agent console
```

### Deploy to Databricks Apps

```bash
databricks apps create databricks-live-voice
databricks apps deploy databricks-live-voice
# Set LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, GENIE_SPACE_ID
# Attach a SQL warehouse via app resources
```

### Headless / Pure Service

See [SERVICE_GUIDE.md](SERVICE_GUIDE.md) for patterns to use the reusable
service layers (`config.py`, `databricks_llm.py`, `genie_tools.py`) without
the demo frontend or token server.

## Project Structure

```
talk-to-your-data/
├── src/
│   ├── config.py            # Credential resolution (App / notebook / local)
│   ├── databricks_llm.py    # FMAPI → OpenAI plugin adapter (zero custom code)
│   ├── genie_tools.py       # Genie integration: 3 modes, narration, voice translation
│   ├── agent.py             # LiveKit voice agent (DatabricksVoiceAgent)
│   ├── main.py              # Demo entrypoint (token server + agent worker)
│   ├── token_server.py      # FastAPI: /api/token, /, /health
│   └── frontend/
│       └── index.html       # 4-state orb UI (data channel driven)
├── tests/
│   └── test_agent.py
├── SERVICE_GUIDE.md         # Headless service patterns & integration guide
├── ARCHITECTURE_PLAN.md     # Full 5-phase roadmap
├── scenarios.yaml           # LiveKit simulation scenarios
├── requirements.txt
├── pyproject.toml
├── app.yaml                 # Databricks App config
└── .env.example
```

See [SERVICE_GUIDE.md](SERVICE_GUIDE.md) for which files are reusable service
layers vs. demo scaffold.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LIVEKIT_URL` | Yes | LiveKit Cloud WebSocket URL |
| `LIVEKIT_API_KEY` | Yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | Yes | LiveKit API secret |
| `DATABRICKS_HOST` | Auto* | Workspace URL |
| `DATABRICKS_TOKEN` | Auto* | PAT or OAuth token |
| `GENIE_SPACE_ID` | No | Genie space for data queries |
| `GENIE_MODE` | No | `agent` (SSE), `chat` (polling), or `mcp` (Genie One) |
| `DATABRICKS_WAREHOUSE_ID` | No | SQL warehouse for Genie |
| `VOICE_PROFILE` | No | premium/balanced/private/hybrid |

*Auto-detected inside Databricks Apps and notebooks.

### Genie Modes

| Mode | API | Narration | Best For |
|------|-----|-----------|----------|
| `agent` | SSE Responses API | Real reasoning text from SSE events | Single-space, lowest latency |
| `chat` | Conversation polling | Status-based (FILTERING_CONTEXT, EXECUTING_QUERY) | Broadest compatibility |
| `mcp` | JSON-RPC 2.0 Genie One | Reasoning from `<details>` blocks | Workspace-wide, multi-space |

### Voice Profiles

| Profile | LLM | STT | TTS | Use Case |
|---------|-----|-----|-----|----------|
| premium | Claude Sonnet 4.6 | AssemblyAI Universal 3.5 | Fish Audio S2.1 Pro | Best quality |
| balanced | GPT-4o Mini | Deepgram Nova 3 | Cartesia Sonic 3 | Low latency |
| private | Llama 3.3 70B | AssemblyAI Universal 3.5 | Fish Audio S2.1 Pro | Open weights |
| hybrid | Claude Sonnet 4.6 | Deepgram Nova 3 | Cartesia Sonic 3 | Quality + speed |

## Narration System

During Genie query processing (10-30s), the agent speaks contextual filler
so the user doesn't hear silence. The narration engine:

- Speaks **real reasoning** when available (extracted from SSE events, MCP
  `<details>` blocks) — e.g. "I'll compare the top franchise groups by
  margin and check their Q1 trend"
- Falls back to **domain-aware filler** from a 60+ phrase pool across 6
  categories (ack, reasoning, querying, results, waiting, routing)
- **Translates technical content** for TTS: strips SQL, removes table names,
  replaces jargon ("aggregate" → "total", "partition by" → "broken down by")
- Uses **two-tier pacing**: 3s gap for real content, 8s for filler
- Tracks used phrases to **prevent repeats** until the pool resets

See [SERVICE_GUIDE.md](SERVICE_GUIDE.md) for full narration customization.

## Frontend Orb States

The 4-state orb is driven by a combination of audio activity and data channel
messages from the agent:

| State | Color | Trigger |
|-------|-------|---------|
| Listening | Red glow | User speaking / idle |
| Understanding | Amber pulse | User stops speaking (1.2s delay) |
| Working | Amber glow | Data channel: `{state: "working", label: "Querying"}` |
| Responding | Blue glow | Agent audio detected |

Working sub-labels: Searching, Analyzing, Querying, Calculating.

## Evaluation

```bash
lk agent simulate --scenarios scenarios.yaml
uv run pytest tests/
```

## Extending

The agent uses the standard LiveKit Agents `@function_tool` pattern. Add new
capabilities by defining tools in `genie_tools.py` or new modules:

- Query a Genie space (built-in, 3 modes)
- Execute SQL against a warehouse
- Search Unity Catalog tables
- Call any Model Serving endpoint
- Trigger a Lakeflow job
- Read from a Vector Search index

## Roadmap

See [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) for the full 5-phase plan.

- **Phase 0** (current): LiveKit + Databricks scaffold, FMAPI, Genie 3-mode,
  narration engine, 4-state orb
- **Phase 1**: Lakebase sessions, OBO identity, multi-tool orchestration
- **Phase 2**: Latency tuning (target 300-450ms p50), advanced turn handling
- **Phase 3**: `@databricks/live-voice` SDK, adapters (Responses API, AgentBricks)
- **Phase 4**: Eval framework, CI/CD latency gates, MLflow Voice Quality Dashboard
