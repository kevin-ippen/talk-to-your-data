# Databricks Live Voice Kit — Architecture & Sequencing Plan

_September 2026. Synthesized from SOTA research, Databricks-specific accelerator POV, Genie for Voice (FEIP-7941), DBVA (Central Engineering), unified-agentic-commerce voice gateway, and LiveKit agent-skills/agent-starter-python ecosystem analysis._

---

## Executive Thesis

**Voice is a presentation layer, not the application.** The customer should be able to take an existing Genie space, agent, dashboard, SQL-backed app, RAG product, or ML application and get excellent live voice with tens of lines of integration code. The expensive realtime engineering becomes shared infrastructure; each customer app contributes only three things: what the user can see, what the assistant can know, and what the assistant can do.

Positioning: _"Add a natural, interruptible live voice interface to any Databricks application."_

---

## Landscape Assessment: What Exists and What to Carry Forward

### LiveKit Agent Ecosystem (livekit/agent-skills, agent-starter-python)
**Key discoveries from GitHub analysis (Sep 2026):**
* **LiveKit Turn Detector** — LiveKit now ships a built-in end-of-turn model combining semantic understanding with acoustic cues, supporting 14 languages. This competes directly with Pipecat Smart Turn v3 and must be evaluated head-to-head.
* **LiveKit Inference** — Zero-config model access through LiveKit credentials (Gemma 4 31B, Fish Audio S2.1 Pro TTS, Deepgram, 50+ models). Simplifies speech model deployment but lacks Databricks UC governance. Use for speech models; keep Databricks FMAPI for LLM/semantic layer.
* **Expressive mode** — Framework injects TTS provider's markup guide into LLM prompt, so model emits inline delivery tags (emotion, pacing, non-verbal sounds) that TTS renders but transcript doesn't show. This IS the "speech realization layer" the SOTA doc describes, but built into the framework rather than custom.
* **`lk agent simulate`** — Mature scenario-based simulation testing with YAML scenario files, LLM judge scoring, risk-based coverage enforcement, adversarial persona generation. Directly usable for Phase 5 eval rather than building from scratch.
* **Agent workflow architecture** — Handoffs (agent-to-agent transitions) and Tasks (scoped operations) as first-class primitives. Maps perfectly to our VoiceApp adapter pattern: each conversation phase gets minimal context.
* **Frontend starters** — React/Next.js, web embed widget, Swift, Flutter, React Native, Android. The `agent-starter-react` and `agent-starter-embed` could seed our `@databricks/live-voice` SDK.
* **Floe Guard** (community) — Open-source cost/budget gating for voice agents. Meters STT+TTS+LLM+telephony per call, hard-stops before ceiling. Relevant for enterprise cost management.

**What this changes in our plan:**
1. Turn detection bake-off: LiveKit Turn Detector vs Smart Turn v3 (not automatic Smart Turn)
2. Adopt `lk agent simulate` for eval harness (Phase 5) instead of building from scratch
3. Use LiveKit Inference for speech models (ASR/TTS) where it simplifies ops; Databricks FMAPI for LLM/semantic
4. Leverage expressive mode rather than building a separate speech realization layer
5. Fork `agent-starter-react` + `agent-starter-embed` as basis for client SDK
6. Integrate Floe Guard pattern for per-session cost tracking

### CRITICAL: LiveKit SDK Already Ships Three of Our Planned Custom Features

Exploring `agent-starter-python/src/agent.py` reveals that the LiveKit Agents SDK (>=1.6.9) provides **first-class primitives** for three capabilities we planned to build from scratch in Phases 2-3:

**1. Adaptive Interruption (was Phase 2 custom overlap state machine)**
```python
interruption={"mode": "adaptive"}
```
LiveKit's adaptive interruption mode uses the turn detector to distinguish real interruptions from backchannels ("mhm", "right"), so the agent keeps talking through the latter. This IS the duck→classify→cancel/restore overlap state machine, built into the framework.

**2. Preemptive/Speculative Generation (was Phase 3 custom speculative execution)**
```python
preemptive_generation={"enabled": True}
```
Allows the LLM to begin generating a response while waiting for end-of-turn confirmation. This is the core of our planned speculative execution controller — stable-prefix → speculative LLM start → promote/discard.

**3. Turn Detection (was Phase 1 Smart Turn integration)**
```python
turn_detection=inference.TurnDetector()
```
LiveKit Turn Detector: end-of-turn model combining semantic understanding with acoustic cues, 14 languages. AgentSession supplies VAD automatically.

**Impact on timeline:** Phases 2-3 (weeks 4-8, the hardest engineering) collapse significantly. The overlap state machine, speculative execution controller, and turn detection become **configuration and tuning** rather than ground-up implementation. We still need to build:
- Played-text tracking (interrupt → truncate conversation history)
- Client-side VAD ducking for sub-60ms perceived responsiveness (browser-side, not server)
- Filler phrase injection during agent latency
- Adaptive endpointing that learns user cadence (extends beyond SDK's built-in)
- Read-only retrieval prefetch (speculative tool execution, not just LLM preemption)

But the core realtime controller work drops from ~4 weeks to ~2 weeks of tuning + extension.

### Agent Architecture Pattern (from agent-starter-python)

The LiveKit Agents SDK pattern is:
```python
class MyAssistant(Agent):
    def __init__(self):
        super().__init__(
            llm=inference.LLM(model="..."),
            instructions="..."
        )

    @function_tool
    async def my_tool(self, context: RunContext, arg: str):
        ...

server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    session = AgentSession(
        stt=inference.STT(model="..."),
        tts=inference.TTS(model="...", voice="..."),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            interruption={"mode": "adaptive"},
            preemptive_generation={"enabled": True},
        ),
        expressive=True,
    )
    await session.start(agent=MyAssistant(), room=ctx.room)
    await ctx.connect()
```

Our VoiceApp contract adapters map directly: each adapter becomes an `Agent` subclass (or wraps one) with `@function_tool` decorators for Databricks operations (Genie queries, UC functions, etc). The `AgentSession` config becomes our YAML profile system.

### Frontend Architecture (from agent-starter-react + agent-starter-embed)

The React frontend uses `@livekit/components-react` which provides:
- `SessionProvider` + `RoomAudioRenderer` (session lifecycle)
- `useVoiceAssistant()` hook (agent state: connecting/initializing/connected/disconnected)
- Pre-built agents-ui components: audio visualizers (aura, bar, grid, radial, wave), chat transcript, control bar, disconnect button, track controls

The embed starter uses Shadow DOM isolation to prevent CSS leakage into host pages — exactly the pattern we need for `@databricks/live-voice` as a drop-in widget.

**Note:** The embed starter is now deprecated in favor of built-in embedding in `@livekit/components-js` (PR #1414). Our `@databricks/live-voice` should wrap the components-js embed directly rather than forking the deprecated starter.

### Evaluation Architecture (from scenarios.yaml + lk agent simulate)

Scenario format:
```yaml
scenarios:
  - label: Descriptive name
    instructions: |
      PERSONA: ...
      OPENING LINE: "..."
      DO, IN ORDER:
      1. ...
    agent_expectations: >
      What success looks like (outcome-based).
    tags:
      feature: category
```

CI integration: GitHub Actions workflow runs `lk agent simulate --scenarios scenarios.yaml` on merge to main. In-process pytest also available via `AgentSession.run()` + `result.expect.judge(judge_llm, intent=...)` for turn-level checks without live sessions.

This maps directly to our eval needs — we add Databricks-specific scenarios (Genie queries, UC tool calls, dashboard context, identity/governance) to this framework.

---

## Concrete Databricks Integration Patterns (from SDK deep-dive)

### 1. Databricks FMAPI as LLM: Zero Custom Code Required

Databricks FMAPI is OpenAI-compatible. The existing OpenAI plugin accepts `base_url` and `api_key`:

```python
from livekit.plugins.openai import LLM

# Databricks FMAPI - works TODAY, no custom plugin needed
llm = LLM(
    model="databricks-gpt-5-4",
    base_url=f"https://{workspace_host}/serving-endpoints",
    api_key=databricks_token,
    temperature=0.7,
    parallel_tool_calls=False,  # important for voice ordering
)
```

Alternatively, for provisioned throughput endpoints:
```python
llm = LLM(
    model="my-pt-endpoint",  # provisioned throughput endpoint name
    base_url=f"https://{workspace_host}/serving-endpoints",
    api_key=databricks_token,
)
```

This means the LLM layer needs **zero custom plugin work**. We use the OpenAI plugin directly with Databricks URLs.

### 2. Databricks-Specific Agent Tasks (Replaces VoiceApp Contract Adapters)

Instead of building a separate VoiceApp contract, we build **`AgentTask` subclasses** — the LiveKit SDK's native pattern for scoped operations:

```python
from livekit.agents import AgentTask, function_tool, RunContext
from databricks.sdk import WorkspaceClient


class GenieQueryTask(AgentTask):
    """Scoped task: query a Genie space, return grounded SQL results."""
    def __init__(self, genie_space_id: str, chat_ctx=None):
        super().__init__(
            instructions="You are querying a data space. Ask the user to clarify "
                        "their question, then call query_genie to get the answer.",
            chat_ctx=chat_ctx,
        )
        self.genie_space_id = genie_space_id

    @function_tool
    async def query_genie(self, ctx: RunContext, question: str) -> str:
        """Query the Genie space with the user's question."""
        w = WorkspaceClient()
        # Use Genie Conversation API
        result = await _query_genie_space(w, self.genie_space_id, question)
        self.complete(result)
        return result.summary


class DashboardContextTask(AgentTask):
    """Scoped task: answer questions about the current dashboard state."""
    def __init__(self, dashboard_state: dict, chat_ctx=None):
        super().__init__(
            instructions=f"The user is looking at a dashboard. Current state: "
                        f"{json.dumps(dashboard_state)}. Help them understand it.",
            chat_ctx=chat_ctx,
        )


class UCFunctionTask(AgentTask):
    """Scoped task: execute a Unity Catalog function."""
    ...
```

This is strictly better than our original VoiceApp contract because:
- Each task gets **minimal context** (latency-critical for voice)
- Tasks have **typed results** (`AgentTask[T]`)
- Tasks can be **composed** via `TaskGroup` (parallel data fetches)
- The agent framework handles interruption, turn-taking, etc. automatically

### 3. Databricks MCP Servers as Native Tools

LiveKit has first-class MCP support. Databricks MCP servers wire in directly:

```python
from livekit.agents import mcp

session = AgentSession(
    ...,
    tools=[
        # Databricks Vector Search via MCP
        mcp.MCPToolset(
            id="databricks_vs",
            mcp_server=mcp.MCPServerHTTP(
                url=f"https://{workspace_host}/api/2.0/mcp/vector-search",
                headers={"Authorization": f"Bearer {token}"},
            ),
        ),
        # Databricks Genie via MCP
        mcp.MCPToolset(
            id="databricks_genie",
            mcp_server=mcp.MCPServerHTTP(
                url=f"https://{workspace_host}/api/2.0/mcp/genie",
                headers={"Authorization": f"Bearer {token}"},
            ),
        ),
        # UC Functions via MCP
        mcp.MCPToolset(
            id="uc_functions",
            mcp_server=mcp.MCPServerHTTP(
                url=f"https://{workspace_host}/api/2.0/mcp/unity-catalog",
                headers={"Authorization": f"Bearer {token}"},
            ),
        ),
    ],
)
```

### 4. Agent Handoffs for Multi-Phase Conversations

The healthcare example shows the exact pattern for our multi-phase voice conversations:

```python
class DatabricksVoiceAgent(Agent):
    """Top-level agent that routes to specialized sub-agents."""
    def __init__(self, genie_space_id: str, dashboard_state: dict = None):
        super().__init__(
            instructions="You are a voice assistant over a Databricks application. "
                        "Use query_data when the user asks about data. "
                        "Use control_dashboard when they want to change the view.",
        )
        self.genie_space_id = genie_space_id
        self.dashboard_state = dashboard_state

    @function_tool
    async def query_data(self, ctx: RunContext, question: str) -> str:
        """Query enterprise data via Genie."""
        result = await GenieQueryTask(
            genie_space_id=self.genie_space_id,
            chat_ctx=self.chat_ctx,
        )
        return result.summary

    @function_tool
    async def control_dashboard(self, ctx: RunContext, action: str, value: str) -> str:
        """Change a dashboard filter or navigate."""
        ctx.disallow_interruptions()  # don't interrupt while changing UI
        # Emit UI event via data channel
        await ctx.room.local_participant.publish_data(
            json.dumps({"type": "ui.action", "action": action, "value": value})
        )
        return f"Done. Changed {action} to {value}."

    async def on_enter(self):
        await self.session.generate_reply(
            instructions="Greet the user and offer to help with the data or dashboard."
        )
```

### 5. Built-in Eval Judges

The SDK ships **8 pre-built evaluation judges** we can use directly:
- `accuracy_judge` — factual correctness
- `coherence_judge` — logical flow
- `conciseness_judge` — voice-appropriate brevity
- `relevancy_judge` — on-topic responses
- `safety_judge` — guardrail compliance
- `task_completion_judge` — did the agent finish the job
- `tool_use_judge` — correct tool selection and parameters
- `handoff_judge` — appropriate agent transitions

We add Databricks-specific judges:
- `governance_judge` — did the agent honor UC permissions / row filters
- `grounding_judge` — are claims backed by actual query results (not hallucinated)
- `latency_judge` — did turns complete within budget

### 6. Self-Hosted LiveKit on Databricks Apps

The deployment topology for a Databricks App:

```python
# Databricks App: app.py
from livekit.agents import AgentServer, cli

server = AgentServer()

@server.rtc_session(agent_name="databricks-voice")
async def voice_agent(ctx: JobContext):
    # Get Databricks workspace client from app context
    w = WorkspaceClient()  # auto-configured in Databricks Apps
    token = w.config.token
    host = w.config.host

    session = AgentSession(
        stt=inference.STT("assemblyai/universal-3-5-pro"),
        llm=LLM(
            model="databricks-gpt-5-4",
            base_url=f"https://{host}/serving-endpoints",
            api_key=token,
        ),
        tts=inference.TTS("fishaudio/s2.1-pro"),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            interruption={"mode": "adaptive"},
            preemptive_generation={"enabled": True},
        ),
        expressive=True,
    )
    await session.start(
        agent=DatabricksVoiceAgent(genie_space_id="..."),
        room=ctx.room,
    )
    await ctx.connect()

if __name__ == "__main__":
    cli.run_app(server)
```

For self-hosted LiveKit (enterprise VPC isolation), the LiveKit server runs alongside or inside the Databricks Apps compute, with WebRTC traffic staying within the VPC.

### 7. Existing voice_gateway.py Patterns That Map to SDK Primitives

| Our Code | SDK Equivalent |
|---|---|
| `VoicePhase.LISTENING/SPEAKING/...` | `useVoiceAssistant()` state: connecting/initializing/listening/thinking/speaking |
| `filler_phrases` dict | `session.say("One sec.", allow_interruptions=False)` |
| `extract_agent_payload()` (Responses API) | SDK handles natively via Agent + function_tool |
| `VoiceGatewayConfig.agent_endpoint` | `LLM(base_url=..., model=...)` |
| `ASRPolicy` / `TTSPolicy` | `inference.STT(model=...)` / `inference.TTS(model=...)` |
| `build_asr_invocation()` / `extract_asr_transcript()` | SDK handles internally |
| `_guard_cart_mutation_response()` | `ToolError("cart write failed")` + LLM handles |
| `CartMutationCallback` | `OrderState.on_change` callback (exactly like drive_thru example) |

### Genie for Voice (Suneel Sunkara / FEIP-7941)
**Strengths to carry forward:**
* Genie API integration patterns (tool-calling to Genie spaces for grounded SQL)
* Multi-lingual support architecture (22 languages)
* MCP server exposure for external orchestrators
* Databricks App delivery with governed data access
* Fine-tuned Whisper LoRA model for domain-specific ASR

**Architectural limitations (do not carry forward):**
* Sequential STT → Genie → TTS cascade: every stage waits for the previous one
* Batch ASR (Whisper) rather than streaming: minimum ~500ms before first transcript
* No interruption handling, no barge-in, no turn-taking intelligence
* No WebRTC: no proper jitter handling, echo cancellation, or full-duplex audio
* Tightly coupled to Genie-only backend

### DBVA (Sabhya Chhabria, Siddharth Murching / Central Engineering)
**Strengths to carry forward:**
* Agent Bricks integration path (PR 1723005)
* App template deployment model (standardized, rapid prototyping)
* Full-duplex interruption via persistent WebSocket
* Extensible tool-calling during live speech

**Architectural limitations (do not carry forward):**
* Hard dependency on OpenAI Realtime API — proprietary, no model control, audio leaves workspace
* No open-weight path for enterprises requiring data sovereignty
* WebSocket-only transport (no WebRTC AEC, jitter handling, or proper media transport)
* No speculative execution, no smart turn-taking

### Unified Agentic Commerce Voice Gateway (Our Prior Work)
**Strengths to carry forward:**
* `voice_policy.py` — clean ASR/TTS adapter abstraction (ASRPolicy, TTSPolicy dataclasses). This is the seed of capability-based model routing.
* `voice_gateway.py` — VoicePhase state machine, filler phrase system, cart mutation detection, agent payload extraction from Responses API format. Good bones.
* MLflow telemetry integration (`telemetry.py`) with trace envelopes, AI Gateway request tags
* Lakebase for durable session state (proven at ~2s e2e)
* Nano Order Executor pattern: deterministic tool execution separated from LLM planning
* Parakeet ASR + Kokoro TTS endpoint deployment notebooks (register_tts_v7, deploy_asr_endpoint)

**Architectural limitations (start fresh):**
* Sequential cascade: record full utterance → batch transcribe → agent call → batch TTS → play. No overlap.
* WebSocket with base64 audio chunks: high latency, no AEC, no proper media transport
* No VAD, no smart turn-taking: fixed buffer approach
* TTS is chunk-buffered, not streaming bi-directional
* No interruption handling (no overlap state machine, no duck-then-classify)
* Tightly coupled to QSR ordering domain

---

## Target Architecture

```
┌─────────────────────────── CLIENT ───────────────────────────┐
│                                                              │
│  React SDK: <VoiceOrb/>, useDatabricksVoice()                │
│                                                              │
│  Mic → WebRTC AEC3 + TEN-VAD WASM → Opus 20ms packets       │
│                    │                                         │
│                    └── local VAD → duck playback (~30-60ms)  │
│                                                              │
│  Speaker ← jitter buffer ← Opus ← generated speech          │
└──────────────────────────┬───────────────────────────────────┘
                           │ WebRTC
                           ▼
┌────────────────── MEDIA PLANE ──────────────────────────────┐
│   LiveKit OSS + Custom Pipecat Controller                    │
│                                                              │
│   ┌─ TEN-VAD (server confirmation)                           │
│   ├─ Smart Turn v3 (acoustic+prosodic EOT, 100-160ms)        │
│   ├─ Overlap State Machine                                   │
│   │     duck → classify(backchannel/takeover) → cancel/restore│
│   ├─ Nemotron 3.5 Streaming ASR (80-160ms selectable)        │
│   │     └─ Parakeet Unified shadow (quality benchmark)        │
│   ├─ Speculative Execution Controller                        │
│   │     ├─ stable prefix → speculative LLM start             │
│   │     ├─ read-only retrieval prefetch                       │
│   │     └─ promote / discard on final transcript              │
│   ├─ Speech Realization (deterministic clause chunking)       │
│   ├─ CosyVoice 3 bi-streaming TTS                           │
│   │     └─ Chatterbox Turbo challenger                        │
│   └─ Played-text tracker (interrupt → truncate history)       │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
┌─── CONTROL PLANE ────────┐  ┌─── DATABRICKS PLATFORM ──────┐
│ Databricks Voice App     │  │                               │
│                          │  │  FMAPI / Provisioned Thru     │
│ • Auth / session boot    │  │  (GPT-OSS-20B, Qwen3-Next)   │
│ • OBO identity           │  │                               │
│ • VoiceApp contract      │  │  Unity AI Gateway             │
│   ├─ GenieAdapter        │  │  (routing, governance)        │
│   ├─ ResponsesAdapter    │  │                               │
│   ├─ AgentBricksAdapter  │  │  UC / MCP / Vector Search     │
│   ├─ LangGraphAdapter    │  │                               │
│   └─ HTTPStreamAdapter   │  │  Model Serving                │
│ • Model routing (YAML)   │  │  (ASR, TTS, Smart Turn)       │
│ • Tool/action bridge     │  │                               │
│ • Conversation state     │  │  Delta / UC telemetry         │
│ • App state (versioned)  │  │                               │
│ • Lakebase sessions      │  │  MLflow tracing               │
│ • MLflow telemetry       │  │                               │
│ • Eval configuration     │  │  Lakebase session metadata    │
└──────────────────────────┘  └───────────────────────────────┘
```

---

## The VoiceApp Contract (Most Important Accelerator Work)

Do not tie the voice layer to any single framework. Define a tiny canonical interface:

```python
class VoiceApp:
    async def respond(
        self, messages, context, tools, user, stream=True
    ) -> AsyncIterator[VoiceResponseEvent]:
        ...

    async def get_context(self) -> dict:
        ...

    async def execute_tool(self, name, args, user):
        ...
```

Response protocol (richer than tokens):
```
text.delta | sentence.commit | tool.start | tool.result
display.update | interruptible | non_interruptible
citation | conversation.state | done
```

Adapters for everything:
```
Genie          → GenieAdapter
Responses API  → ResponsesAdapter
Agent Bricks   → AgentBricksAdapter (from DBVA patterns)
LangGraph      → LangGraphAdapter
Custom HTTP    → HTTPStreamingAdapter
```

---

## Three Integration Levels

### Level 1 — Voice-wrap my existing chat (~15 minutes)
Customer has `POST /chat {messages}`. Voice Kit handles STT → existing /chat → streaming text → speech planner → TTS. They immediately get ASR, TTS, VAD, interruption, Smart Turn, transcript display, audio device management, session state, telemetry. Covers ~80% of Databricks Apps.

### Level 2 — Voice-aware agent
Customer exposes application context (screen state, filters, selections, user identity). User says "Why did this one drop so much?" and voice knows what "this one" refers to. Voice stops being a bolted-on microphone.

### Level 3 — Full multimodal copilot
Voice agent emits UI events: `ui.set_filter`, `ui.highlight`, `ui.navigate`. "Show me just Michigan" changes the dashboard. "Which five should I be worried about?" produces analysis. "Open the worst one" navigates. Voice + visual co-presentation: agent decides what to SPEAK vs DISPLAY vs BOTH.

---

## Component Choices (Opinionated)

| Layer | Primary | Why | Challenger |
|---|---|---|---|
| Media transport | LiveKit OSS / WebRTC | Full-duplex RTP, jitter, mobile/browser SDKs, SIP later | — |
| Pipeline | Pipecat (heavily customized) | Excellent OSS realtime primitives; don't let defaults dictate UX | — |
| Echo cancellation | WebRTC AEC3 | Mandatory for true barge-in while AI speaks | — |
| VAD | TEN-VAD | Tiny, realtime, WASM for browser, better PR than Silero | — |
| Turn completion | **Bake-off: LiveKit Turn Detector vs Smart Turn v3** | LK: built-in, 14 languages, semantic+acoustic. ST3: Pipecat-native, BSD, ~10ms CPU | Winner becomes default |
| Streaming ASR | Nemotron 3.5 Streaming 0.6B | Runtime-selectable latency 80ms+, cache-aware, 240 streams/H100 | Parakeet Unified EN 0.6B (quality shadow) |
| Fast LLM | GPT-OSS-20B on Databricks | Open weights, tool capable, streaming | Qwen3-Next 80B-A3B |
| Quality LLM | Gemini Flash via FMAPI | Quality ceiling when open weights isn't absolute | — |
| TTS | CosyVoice 3 | Bi-streaming, prosody/voice cloning, Apache 2.0 | Chatterbox Turbo (350M, MIT), Fish Audio S2.1 Pro (via LK Inference) |
| Experimental duplex | Moshi | Full-duplex speech foundation model for R&D benchmark | — |
| Session state | Lakebase | Proven in commerce app, scale-to-zero, UC integration | — |
| Telemetry | MLflow + Delta | Already integrated, trace-native | — |

### Model Profiles (capability-based, not model-locked)

```yaml
asr:
  capability: streaming_asr
  profile: quality
tts:
  capability: streaming_tts
  voice: alloy-like-neutral
turn_detection:
  profile: conversational
llm:
  endpoint: customer-agent
```

| Profile | ASR | TTS | LLM | Target |
|---|---|---|---|---|
| Premium | Nemotron Streaming | CosyVoice 3 | FMAPI | Best UX |
| Balanced | Nemotron/Parakeet | Chatterbox | GPT-OSS-20B | Lower GPU |
| Private | All OSS | All OSS | OSS only | No external API |
| Hybrid | OSS speech | OSS speech | FMAPI | Best semantic quality |

---

## Critical Interaction Design

### Interruption: Duck First, Cancel Second

```
                    user speech detected
                           │
                           ▼
                DUCK output immediately (~12-18 dB)
                           │
              ┌────────────┴────────────┐
              │                         │
        ~50-150ms audio           clear command
              │                  "wait", "no", etc.
              ▼                         │
      overlap classifier                ▼
      + partial ASR             HARD CANCEL
             /          \
            /            \
   BACKCHANNEL           TAKEOVER
   "yeah"/"mhm"        actual new turn
        │                    │
        ▼                    ▼
   restore audio        HARD CANCEL
   + crossfade          assistant generation
```

Browser VAD triggers ducking locally (~30-60ms), not via server round-trip. TEN-VAD WASM is the key enabler.

Hard-cancel keyword detector runs locally: `stop`, `wait`, `hold on`, `no`, `actually`, `hang on`.

### Turn-Taking: Adaptive, Not Fixed

No fixed 500ms endpointing timer (instant Alexa feel). Instead:

```python
if silence > 120ms:
    if smart_turn.p_finished > 0.92:
        commit_turn()
elif silence > adaptive_max_delay:  # learns user cadence
    commit_turn()
```

Fast talker settles near 250ms. Someone who pauses mid-sentence settles near 450ms.

### Speculative Execution (Biggest Latency Trick)

```
USER SPEAKING
───────────────────────────────────>
ASR       ████████████████████████████
                    │
                    └─ stable prefix
LLM                ████████████████
                   speculative ^
TTS                                 ███████
                                    committed only
```

If final transcript semantically matches, promote. If it materially changes, discard and restart. Side-effecting tools NEVER execute speculatively. Read-only retrieval prefetches eagerly.

### Conversation History Must Reflect What User Heard

On interruption, truncate assistant history to last confirmed played word (via RTP timestamp → word alignment tracking). This fixes bizarre post-interruption behaviors.

### TTS: Clause Chunks, Not Tokens

First chunk: 3-7 words when acoustically safe. Subsequent: 6-18 words. Break at punctuation > syntax boundary > prosody prediction. CosyVoice 3 bi-streaming enables text-in-streaming + audio-out-streaming simultaneously.

---

## Deployment Topology

```
WORKSPACE

Customer App A ─┐
Customer App B ─┼────► Voice Accelerator App (shared)
Customer App C ─┘              │
                         session / control
                               │
                    external/VPC media plane
                               │
                    LiveKit + speech workers
                               │
                  ┌────────────┴────────────┐
                  ▼                         ▼
            Model Serving              FMAPI / Agents
```

Databricks app-to-app resource pattern: shared `voice-runtime` app, not a copy per customer. Installation:

```
1. Deploy Voice Accelerator App
2. Add it as resource to Customer App
3. npm/pip install voice client
4. Register app adapter
5. Done
```

### Identity Propagation

Browser → authenticate → Voice App → issue ephemeral WebRTC token → bind session_id to Databricks user. Media plane gets opaque session_id only. Data operations flow through Voice App with OBO (on-behalf-of-user) — UC row filters and column masks honored. Audio credentials never reach LiveKit.

### Media Plane Deployment Options

```
Databricks-hosted media plane  (default)
          OR
Customer VPC media plane       ("audio cannot leave our VPC")
          OR
LiveKit Cloud                  (fastest setup)
          OR
Customer K8s / VM              (full control)
```

---

## Latency Budget (Release Gates, Not Aspirations)

| Metric | 5/5 Target |
|---|---|
| Mic packetization | 20ms |
| Local speech-onset detection | <40ms |
| Playback duck after user starts | p50 <60ms |
| Hard barge-in stop | p50 <120ms, p95 <180ms |
| Streaming ASR chunk | 80-160ms |
| ASR partial availability | <150ms behind speaker |
| EOT decision after true ending | p50 150-250ms |
| LLM first token | <150ms ideal |
| TTS first audio after text | 100-180ms |
| **End-of-user → first audible AI** | **p50 300-450ms** |
| End-of-user → first audible AI p95 | <650ms |
| False early EOT | <1-2% |
| Backchannel-induced cancellation | <1% |
| Audible TTS splice defects | Effectively zero |

The 300-450ms target comes from overlapping, not from fast sequential:

```
                USER SPEAKING
────────────────────────────────────────────>
ASR         █████████████████████████████
Turn detect                   ███████
Retrieval              ███████████████
Spec LLM                   █████████████
                              │ COMMIT
Speech planner                 ███
TTS                              █████
Audio                                ▶
```

---

## Phased Build Sequence

### Phase 0: Media Foundation (Week 1-2)
**Goal:** WebRTC audio flowing, speech models deployed, basic echo test.

* [ ] Stand up LiveKit OSS dev instance (Docker, single node)
* [ ] Deploy Nemotron 3.5 Streaming ASR on Model Serving (route-optimized, NO scale-to-zero)
* [ ] Deploy CosyVoice 3 on Model Serving (route-optimized)
* [ ] Port `voice_policy.py` → capability-based ASRPolicy/TTSPolicy with provider abstraction
* [ ] Basic Pipecat pipeline: WebRTC → ASR → echo-back-as-text
* [ ] TEN-VAD WASM integration in browser (local duck test)
* [ ] Define VoiceApp contract (Python abstract class + response event types)
* [ ] Wire MLflow tracing skeleton (reuse `telemetry.py` patterns)

**Reuse:** voice_policy.py adapter pattern, telemetry.py trace envelope, deploy_asr/tts notebooks.

### Phase 1: Working Cascade (Week 2-4)
**Goal:** End-to-end voice conversation with a real Databricks backend. Sequential but complete.

* [ ] Full Pipecat pipeline: VAD → streaming ASR → LLM → speech planner → TTS → WebRTC
* [ ] Turn detection bake-off: LiveKit Turn Detector vs Smart Turn v3 (run both, measure false EOT rate)
* [ ] Clause-aware TTS chunking (not token-by-token)
* [ ] Build GenieAdapter (from Genie for Voice patterns)
* [ ] Build HTTPStreamingAdapter (for existing `/chat` endpoints)
* [ ] Databricks App as control plane: auth, session bootstrap, config
* [ ] OBO identity propagation for all data operations
* [ ] Lakebase session metadata store (reuse commerce Lakebase patterns)
* [ ] Conversation state management
* [ ] Basic voice configuration (YAML: language, persona, model endpoints)
* [ ] **Target: 600-800ms e2e p50**

**Reuse:** Genie API integration from Genie for Voice, Lakebase patterns from commerce app, filler phrase system from voice_gateway.py.

### Phase 2: Tuning & Extension (Week 4-6) — COMPRESSED (was Phases 2+3)
**Goal:** Tune SDK primitives to 5/5 conversational feel + build what the SDK doesn't cover.

The LiveKit Agents SDK already provides adaptive interruption (`interruption={"mode": "adaptive"}`), preemptive/speculative generation (`preemptive_generation={"enabled": True}`), and turn detection (`inference.TurnDetector()`). This phase is **tuning + extension**, not ground-up implementation.

**SDK tuning (configuration, not code):**
* [ ] Tune adaptive interruption thresholds for enterprise speech patterns (longer pauses, domain jargon)
* [ ] Tune preemptive generation aggressiveness (how early to commit)
* [ ] Tune TurnDetector sensitivity for domain-specific turn patterns
* [ ] Benchmark LiveKit TurnDetector vs Smart Turn v3 on our adversarial audio corpus
* [ ] Evaluate LiveKit expressive mode with CosyVoice 3 / Fish S2.1 Pro / Chatterbox

**Custom extensions (what the SDK doesn't cover):**
* [ ] Played-text tracking: word alignment → truncate conversation history on interrupt
* [ ] Client-side VAD ducking via TEN-VAD WASM (~30-60ms perceived) — browser-side, below SDK layer
* [ ] Local keyword detector for hard-cancel phrases ("stop", "wait", "no")
* [ ] Filler phrase injection during agent latency (evolve from voice_gateway.py system)
* [ ] Read-only retrieval prefetch: speculative UC/Genie/Vector Search on stable ASR prefix
* [ ] Side-effect guard: tools marked `safe_to_speculate` vs `requires_commit`
* [ ] Adaptive endpointing extension: learn user's speech cadence per session (beyond SDK built-in)
* [ ] Shadow ASR (Parakeet Unified) for quality benchmarking
* [ ] **Target: 300-450ms p50, <650ms p95, <1% backchannel cancellation**

**Reuse:** VoicePhase state machine from voice_gateway.py, filler phrase system.

### Phase 3: SDK & Adapters (Week 6-8) — MOVED UP (was Phase 4)
**Goal:** Any SA can bolt this onto a customer demo in an afternoon.

* [ ] Build on `@livekit/components-react` + upcoming components-js embed (PR #1414, replaces deprecated agent-starter-embed)
* [ ] Build `useDatabricksVoice()` hook wrapping LiveKit's `useVoiceAssistant()` + Databricks OAuth
* [ ] Drop-in components: `<VoiceOrb/>`, `<VoiceTranscript/>`, `<VoiceStatus/>`, `<VoiceButton/>`
* [ ] Headless mode for custom UIs
* [ ] npm package: `@databricks/live-voice` (wraps `@livekit/components-react` + Databricks OAuth + app-to-app token exchange)
* [ ] App-to-app resource pattern (shared voice-runtime, not per-customer copy)
* [ ] ResponsesAdapter (for Mosaic AI Agent Framework)
* [ ] AgentBricksAdapter (leverage DBVA integration patterns from PR 1723005)
* [ ] LangGraphAdapter
* [ ] Three integration levels documented with examples
* [ ] Voice + visual co-presentation protocol (SPEAK / DISPLAY / BOTH)
* [ ] Client tools via WebRTC data channel (`ui.set_filter`, `ui.highlight`, `ui.navigate`)
* [ ] Server tools / client tools / hybrid tools bridge
* [ ] Tool registration: `voice.registerTool({name, description, schema, execute})`
* [ ] Application state interface: `voice.updateContext({page, selections, filters})`
* [ ] YAML configuration system (personas, models, retention, interruption, telemetry)

### Phase 4: Evaluation & Hardening (Week 8-10) — MOVED UP (was Phase 5)
**Goal:** Ship an evaluation package, not just software.

**Adopt `lk agent simulate` as eval backbone:**
* [ ] Write `scenarios.yaml` using LiveKit simulation format (persona + goals + agent_expectations)
* [ ] Build `risks.yaml` with coverage enforcement (unavailable, withhold-required, invalid-value, out-of-scope, harmful, sensitive-data, prompt-extraction per livekit-simulations skill)
* [ ] Wire `lk agent simulate` into CI (GitHub Actions workflow on merge to main)
* [ ] Extend with voice-specific acoustic tests that `lk agent simulate` doesn't cover (actual audio adversarial corpus)

* [ ] MLflow Voice Quality Dashboard with all metrics:
  * EOT → first audio (p50/p95)
  * Barge-in → assistant stop (p50/p95)
  * ASR WER, named-entity accuracy
  * False/missed interruption rate, false EOT rate
  * TTS first-audio latency, audio underrun rate
  * LLM TTFT, tool latency
  * Voice-session cost, GPU utilization
  * Conversation completion, user retries
* [ ] Prerecorded adversarial test suite:
  * Long hesitation, "uhhh...", rapid speech
  * Background TV, poor mic, accented English
  * Proper nouns, numbers, dates
  * Interrupt at 100ms, interrupt at 500ms
  * "Yeah" backchannel, "No wait..."
  * Speaker echo, two people speaking
* [ ] Model A/B framework: Nemotron vs Parakeet, CosyVoice vs Chatterbox
* [ ] Audio retention policies: NEVER / DEBUG_ONLY / OPT_IN / ALWAYS
* [ ] Latency gates as CI/CD release criteria
* [ ] GPU utilization optimization (route-optimized endpoints, no scale-to-zero on critical path)
* [ ] Security review: audio never reaches LiveKit with Databricks credentials

### Phase 5: Challenger Experiments (Ongoing) — RENUMBERED

```
ASR:   Nemotron 3.5 80/160ms  vs  Parakeet Unified 240/320ms
TTS:   CosyVoice 3            vs  Chatterbox Turbo
Arch:  Cascade                 vs  Moshi-derived full-duplex hybrid
```

The Moshi track: full-duplex audio model learns when to breathe, yield, overlap, acknowledge, pause, laugh, and interrupt. Semantic LLM brain determines what to say. That hybrid is where open systems eventually surpass today's cascade. Run it as R&D, not production.

**Per-session cost tracking (from Floe Guard pattern):**
```
STT cost + TTS cost + LLM cost + transport cost = session cost
→ real-time budget gate: hard-stop next turn before ceiling
→ log to Delta/MLflow for cost-per-session analytics
```

---

## Accelerator Repo Structure

```
databricks-live-voice/
├── client/
│   ├── react/                  # useDatabricksVoice(), VoiceOrb, VoiceTranscript
│   ├── javascript/             # Vanilla JS SDK
│   └── web-components/         # Framework-agnostic
├── runtime/
│   ├── voice-session/          # Session lifecycle
│   ├── turn-controller/        # Smart Turn + adaptive endpointing
│   ├── interruption/           # Overlap state machine
│   ├── speech-planner/         # Clause chunking + realization
│   ├── speculative/            # Speculative execution controller
│   └── conversation-state/     # Dual state: conversation + app
├── adapters/
│   ├── genie/
│   ├── responses-api/
│   ├── agent-bricks/
│   ├── langgraph/
│   ├── openai-compatible/
│   └── custom-http/
├── models/
│   ├── nemotron-asr/
│   ├── smart-turn/
│   ├── cosyvoice/
│   └── chatterbox/
├── media/
│   └── livekit/
├── databricks/
│   ├── app/                    # Voice Accelerator Databricks App
│   ├── asset-bundle/           # DAB for deployment
│   ├── uc/                     # Unity Catalog integration
│   ├── mlflow/                 # Tracing + eval
│   └── dashboards/             # Voice Quality Dashboard
├── evaluation/
│   ├── recordings/             # Adversarial audio test corpus
│   ├── interruption-tests/
│   ├── latency-tests/
│   └── quality-evals/
└── examples/
    ├── voice-genie/            # Level 1: wrap a Genie space
    ├── voice-dashboard/        # Level 2: voice-aware dashboard
    ├── voice-agent/            # Level 2: voice-aware agent
    ├── voice-commerce/         # Level 3: full multimodal (from our QSR work)
    └── voice-digital-twin/     # Level 3: multimodal copilot
```

---

## Key Architectural Decisions

1. **LiveKit OSS, not LiveKit Cloud** — Self-host for enterprise "audio never leaves VPC" story. Customer VPC deployment is a config change, not a fork.

2. **Pipecat as primitive layer, heavily customized** — Use its realtime frame processing but override turn-taking, interruption, and TTS scheduling entirely. Don't let Pipecat defaults dictate UX.

3. **Media plane separate from Databricks Model Serving REST** — Model Serving is excellent for low-latency REST inference, but native conversational audio wants long-lived, session-affine, bidirectional transport. Databricks Apps are the right host for persistent connections.

4. **Voice Accelerator as shared Databricks App** — App-to-app resource pattern. One voice infrastructure deployment serves many customer apps. Not a copy of the voice stack per application.

5. **VoiceApp contract, not framework lock-in** — Any backend (Genie, Responses API, LangGraph, custom FastAPI) can implement respond/context/tools. Much more valuable than prescribing one agent architecture.

6. **Open weights default, FMAPI fallback** — Nemotron + CosyVoice + GPT-OSS-20B for full open-weight path. Gemini Flash via FMAPI as quality ceiling. Customer never forced into a proprietary dependency.

7. **Evaluation ships with the accelerator** — Adversarial test suite and Voice Quality Dashboard are not Phase 2 nice-to-haves. They're part of the product. Latency gates are release criteria.

8. **Leverage the LiveKit Agents SDK's realtime controller, invest engineering in Databricks integration** — None of the individual model choices determines whether this gets a 5/5. Nemotron + CosyVoice with mediocre orchestration still feels like a voice bot. Speculative execution, sub-100ms yielding, backchannel discrimination, played-text tracking, adaptive cadence — that's what makes it feel like ChatGPT Live.

---

## Risk Register

| Risk | Mitigation |
|---|---|
| LiveKit OSS complexity / ops burden | Start with Docker single-node. Evaluate LiveKit Cloud as escape hatch for non-VPC-constrained customers. |
| Nemotron 3.5 ASR quality on domain-specific terms | Shadow Parakeet Unified continuously. Fine-tune ASR on domain vocabulary if needed. |
| CosyVoice 3 latency on first chunk | Run Chatterbox Turbo bake-off in parallel. Target <150ms TTFA. |
| GPU cold starts on speech models | Route-optimized endpoints, NO scale-to-zero on ASR/TTS. Filler phrases bridge sub-second agent latency. |
| Speculative execution complexity | Build Phase 3 only after Phase 1-2 are solid. Speculative is pure additive — cascade works without it. |
| Enterprise audio retention / compliance | First-class YAML config: NEVER / DEBUG_ONLY / OPT_IN / ALWAYS. Default to NEVER. |
| Identity propagation edge cases | OBO is proven in Databricks Apps. Test with UC row filters + column masks early. |

---

## What the Demo Looks Like

The killer demo is Level 3 on a Databricks dashboard or app:

1. User says: _"Compare the five worst stores."_
2. Dashboard filters. Agent says: _"Store 4812 is the biggest concern — traffic fell six percent and labor costs climbed four."_ Meanwhile a comparison table appears.
3. User says: _"Why?"_ (voice knows context)
4. Agent: _"The weather data shows three major storm days in Q2, and there was a competing promotion from..."_
5. User says: _"Yeah—"_ (backchannel, agent continues without stopping)
6. User says: _"Actually, show me just Michigan."_
7. Dashboard re-filters. Agent: _"Michigan's actually outperforming the region average. The issue is concentrated in..."_
8. User says: _"Open the worst one."_
9. Navigation happens.

That is not an Alexa clone. That is a multimodal copilot over governed enterprise data. Identity-aware, interruptible, contextual, and running entirely within the customer's Databricks workspace.

---

_"The features that get you from 'excellent voice bot' to 'this feels like ChatGPT Live' are speculative turn execution, sub-100ms perceived yielding, backchannel-vs-takeover discrimination, played-text-aware cancellation, acoustic endpointing, clause-aware streaming TTS, adaptive conversational cadence, and truly simultaneous listen/speak operation. If I were allocating engineering effort, I'd put roughly half of it into the realtime controller and evaluation harness, not the models themselves."_
