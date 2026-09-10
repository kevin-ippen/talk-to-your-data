# Databricks Live Voice Kit

Add a natural, interruptible live voice interface to any Databricks application.
Ask questions about your data by speaking — get grounded answers from Genie spaces,
dashboards, Unity Catalog, and any Databricks-hosted agent.

## Architecture

```
┌───────────────────┐     WebRTC (Opus)     ┌───────────────────┐
│  Browser Client   │────────────────────▶│  LiveKit Cloud    │
│  (index.html)     │                     │  (media server)   │
└─────────┬─────────┘                     └─────────┬─────────┘
          │ POST /api/token                      │ WebSocket
          ▼                                        ▼
┌─────────────────────────────────────────────────────┐
│  Databricks App (main.py)                           │
│  ┌─────────────────────┐  ┌───────────────────────┐  │
│  │  Token Server      │  │  Agent Worker        │  │
│  │  (FastAPI)         │  │  (LiveKit Agents)    │  │
│  │  - /api/token      │  │  - STT (inference)   │  │
│  │  - / (frontend)    │  │  - LLM (FMAPI)      │  │
│  │  - /health         │  │  - TTS (inference)   │  │
│  └─────────────────────┘  │  - Genie tools      │  │
│                           └─────────┬─────────────┘  │
└───────────────────────────┬─┴─────────────────────────┘
                            │
                ┌─────────┴─────────────┐
                │  Databricks Platform    │
                │  - FMAPI (Claude/GPT)   │
                │  - Genie Spaces         │
                │  - Unity Catalog        │
                │  - AI Gateway           │
                └───────────────────────┘
```

## Quick Start

### Prerequisites

1. **LiveKit Cloud account** (free tier): https://cloud.livekit.io/
   - Create a project → get your `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

2. **Databricks workspace** with:
   - Foundation Model API access (any pay-per-token model)
   - (Optional) A Genie space to query

3. **Python 3.10+** and **uv** (recommended) or pip

### Local Development

```bash
cd talk-to-your-data
cp .env.example .env.local
# Edit .env.local with your LiveKit + Databricks credentials

pip install -r requirements.txt   # or: uv sync

# Option A: Full stack (token server + agent + frontend)
python -m src.main
# Open http://localhost:8000

# Option B: Agent only (use LiveKit Agents Playground)
python -m src.agent dev
# Open https://agents-playground.livekit.io

# Option C: Text mode (no audio, terminal only)
python -m src.agent console
```

### Deploy to Databricks Apps

```bash
# Create the app (first time only)
databricks apps create databricks-live-voice

# Deploy source code
databricks apps deploy databricks-live-voice

# Configure env vars in the Databricks Apps UI:
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
#   GENIE_SPACE_ID (optional)
# Attach a SQL warehouse via app resources.
```

## Project Structure

```
talk-to-your-data/
├── src/
│   ├── main.py            # Entrypoint: runs token server + agent worker
│   ├── agent.py           # LiveKit voice agent (DatabricksVoiceAgent)
│   ├── token_server.py    # FastAPI: /api/token, /, /health
│   ├── databricks_llm.py  # FMAPI via OpenAI plugin (zero custom code)
│   ├── genie_tools.py     # Genie space query as function_tool
│   ├── config.py          # DatabricksConfig + VoiceConfig profiles
│   └── frontend/
│       └── index.html     # Voice UI (LiveKit JS SDK, no build step)
├── tests/
│   └── test_agent.py      # In-process agent tests
├── scenarios.yaml         # LK simulation scenarios (lk agent simulate)
├── requirements.txt       # Databricks App deps (pip)
├── pyproject.toml         # Dev/CI deps (uv)
├── Dockerfile
├── app.yaml               # Databricks App config
└── .env.example
```

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
| `DATABRICKS_WAREHOUSE_ID` | No | SQL warehouse for Genie |
| `VOICE_PROFILE` | No | premium/balanced/private/hybrid |
| `PORT` | No | HTTP port (default: 8000) |

*Auto-detected inside Databricks Apps and notebooks.

### Voice Profiles

| Profile | LLM | STT | TTS | Use Case |
|---------|-----|-----|-----|----------|
| premium | Claude Sonnet 4.6 | AssemblyAI Universal 3.5 | Fish Audio S2.1 Pro | Best quality |
| balanced | GPT-4o Mini | Deepgram Nova 3 | Cartesia Sonic 3 | Low latency |
| private | Llama 3.3 70B | AssemblyAI Universal 3.5 | Fish Audio S2.1 Pro | Open weights |
| hybrid | Claude Sonnet 4.6 | Deepgram Nova 3 | Cartesia Sonic 3 | Quality + speed |

## Evaluation

```bash
# Run simulation scenarios
lk agent simulate --scenarios scenarios.yaml

# Run in-process tests
uv run pytest tests/
```

## Extending

The agent uses the standard LiveKit Agents `@function_tool` pattern. Add new
Databricks capabilities by defining tools in `genie_tools.py` or creating
new tool modules:

- Query a Genie space (built-in)
- Execute SQL against a warehouse
- Search Unity Catalog tables
- Call any Model Serving endpoint
- Trigger a Lakeflow job
- Read from a Vector Search index

## Roadmap

See [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) for the full 5-phase plan.

- **Phase 0** (current): LiveKit + Databricks scaffold, FMAPI integration
- **Phase 1**: Working cascade, Genie adapter, Lakebase sessions, OBO identity
- **Phase 2**: Latency tuning (target 300-450ms p50), advanced turn handling
- **Phase 3**: `@databricks/live-voice` SDK, adapters (Responses API, AgentBricks, LangGraph)
- **Phase 4**: Eval framework, CI/CD latency gates, MLflow Voice Quality Dashboard
