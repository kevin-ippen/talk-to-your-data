Yes. As of September 2026, I think you can build something that gets **surprisingly close to ChatGPT Live**, but I would not implement it as a conventional sequential `VAD → STT → LLM → TTS` pipeline. That architecture can sound excellent, but it will never feel excellent because every stage waits for the previous one.

I would build a **speculative, full-duplex cascade with an audio-native conversation controller**, while running a parallel speech-to-speech research track. The stack below is the one I would actually bet on.

## The architecture I would ship

```text
┌──────────────────────────── CLIENT ────────────────────────────┐
│                                                               │
│ Mic → WebRTC AEC/NS/AGC → TEN-VAD WASM → Opus 20 ms packets  │
│                         │                                     │
│                         └── local barge-in / playback ducking  │
│                                                               │
│ Speaker ← jitter buffer ← Opus ← generated speech             │
└──────────────────────────────┬────────────────────────────────┘
                               │ WebRTC
                               ▼
┌──────────────────── REALTIME SPEECH EDGE ─────────────────────┐
│     LiveKit OSS + lightweight Pipecat coordinator             │
│                                                               │
│  jitter/audio clock                                           │
│      │                                                        │
│      ├── TEN-VAD                                              │
│      ├── Smart Turn v3 ─────────────┐                         │
│      └── Nemotron 3.5 Streaming ASR │                         │
│                                     ▼                         │
│                       conversational state machine             │
│                      /          │             \                │
│             speculative LLM   normal LLM      cancel           │
│                      \          │             /                │
│                       streamed semantic text                   │
│                              │                                │
│                       prosody chunker                          │
│                              │                                │
│                       CosyVoice 3                              │
│                              │                                │
│                       PCM → Opus → RTP                          │
└──────────────────────────────┬────────────────────────────────┘
                               │
              ┌────────────────┴─────────────────┐
              ▼                                  ▼
┌──────────── DATABRICKS ────────────┐   ┌── SPEECH GPU POOLS ──┐
│                                    │   │                       │
│ FMAPI / provisioned throughput     │   │ Nemotron ASR         │
│ Unity Gateway                      │   │ CosyVoice / Chatterbox│
│ UC tools + MCP                     │   │                       │
│ Vector Search                      │   │ L4/A10/H100           │
│ MLflow tracing + eval              │   └───────────────────────┘
│ Delta / UC conversation telemetry  │
│ Lakebase durable session metadata  │
└────────────────────────────────────┘
```

The small exception to "everything Databricks" is deliberate: **I would not force continuous stateful audio through Databricks Model Serving.** Model Serving is an excellent low-latency REST inference system and route optimization dramatically reduces network overhead, but native conversational audio wants a long-lived, session-affine, bidirectional transport. Databricks Apps are explicitly the native choice for persistent app connections, while Model Serving is REST/API based. ([Databricks Documentation][1])

So I would keep a very thin stateful speech edge and put the expensive intelligence, governance, data, tools, observability, evaluation, and much of the compute estate on Databricks.

---

## My component choices

| Layer                    | Primary                                                      | Why                                                                                             |
| ------------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Media                    | **LiveKit OSS / WebRTC**                                     | Proper full-duplex RTP, jitter handling, mobile/browser SDKs, SIP later                         |
| Pipeline                 | **Pipecat**, customized heavily                              | Excellent OSS realtime primitive layer; don't let its defaults dictate your UX                  |
| Echo cancellation        | **WebRTC AEC3**                                              | Mandatory for true barge-in while the AI is speaking                                            |
| VAD                      | **TEN-VAD**                                                  | Tiny, realtime, available in WASM; authors report better PR characteristics than WebRTC/Silero  |
| Turn completion          | **Pipecat Smart Turn v3**                                    | Audio-native semantic/prosodic endpointing, BSD licensed, tens-of-ms CPU inference              |
| Streaming ASR            | **Nemotron 3.5 ASR Streaming 0.6B**                          | This is the standout component right now                                                        |
| ASR quality shadow       | **Parakeet Unified EN 0.6B**                                 | Excellent English final transcript / correction path                                            |
| Fast LLM                 | **GPT-OSS-20B or Qwen3-Next 80B-A3B Instruct on Databricks** | Open weights, tool capable, suitable for streaming                                              |
| Max-quality optional LLM | Gemini 3.8 Flash via Databricks                              | If "open weights" isn't absolute, use it as your quality ceiling                                |
| TTS                      | **CosyVoice 3**                                              | Bi-streaming, strong prosody/voice cloning, Apache 2.0                                          |
| TTS challenger           | **Chatterbox Turbo**                                         | 350M, designed for realtime agents, expressive tags, MIT                                        |
| TTS quality benchmark    | Fish Speech S2 Pro                                           | ~100 ms claimed TTFA and superb expressiveness, but research license complicates commercial use |
| Experimental duplex      | **Moshi**                                                    | Full-duplex speech foundation model; useful as a research benchmark/controller                  |

Nemotron 3.5 ASR is unusually well matched to this problem: NVIDIA exposes runtime-selectable latency from roughly **80 ms upward**, uses cache-aware FastConformer rather than repeatedly recomputing buffered context, and reports 240 concurrent streams on one H100 even at its lowest-latency setting. ([Hugging Face][2])

If your product is English-only, I would simultaneously benchmark Parakeet Unified at **240–320 ms**, because its quality curve is extremely attractive there; its model card shows much better WER at 240 ms than at its extreme 160 ms setting. ([Hugging Face][3])

For TTS, CosyVoice 3 currently looks like my safest production bet. It explicitly supports **text-in streaming + audio-out streaming**, claims latency as low as ~150 ms, gives controllable emotion/speed/dialect behavior, and its repo uses Apache 2.0. ([GitHub][4])

Chatterbox Turbo absolutely belongs in the bake-off. It is only 350M parameters, replaces its previous 10-step speech decoder with a one-step decoder, supports things like `[laugh]` and `[chuckle]`, and is MIT licensed. ([GitHub][5])

---

# The critical part: interruption cannot mean “VAD fired, stop”

This is where I think most otherwise-good voice implementations fall from a 5/5 to a 3/5.

Suppose the AI says:

> “The reason your forecast changed is actually pretty interesting—”

and the human says:

> “Yeah.”

A dumb barge-in implementation stops the AI.

Humans don't.

But if they say:

> “No, hang on—”

the AI should stop virtually instantly.

So while `assistant.speaking == true`, I would implement a separate **overlap state machine**:

```text
                       user speech detected
                               │
                               ▼
                    DUCK output immediately
                         ~12-18 dB
                               │
                   ┌───────────┴───────────┐
                   │                       │
             ~50-150ms audio           clear command
                   │                  "wait", "no", etc.
                   ▼                       │
          overlap classifier               ▼
          + partial ASR               HARD CANCEL
           /          \
          /            \
 BACKCHANNEL           TAKEOVER
 "yeah"/"mhm"        actual new turn
      │                   │
      ▼                   ▼
 restore audio        HARD CANCEL
 + crossfade          assistant generation
```

**Duck first; cancel second.**

That gives you immediate apparent responsiveness without destroying the conversation every time somebody says “right.”

I would additionally run a tiny local keyword detector for:

`stop`, `wait`, `hold on`, `no`, `actually`, `hang on`

because those can hard-cancel before full ASR resolution.

And the browser's VAD should trigger the ducking locally. You don't want:

```text
mic → internet → server → decision → internet → stop playback
```

to be your interrupt path.

You want:

```text
mic → local VAD → duck
```

in perhaps 30–60 ms.

TEN-VAD is particularly interesting here because it has a browser/WASM implementation and is explicitly targeted at low-compute realtime detection. ([GitHub][6])

---

# Turn-taking matters even more than ASR

A fixed 500 or 700 ms endpointing timer instantly makes the experience feel like Alexa.

Instead:

```text
                   speech stops
                       │
             ┌─────────┴─────────┐
             │                   │
          acoustic           semantic /
          evidence           prosodic evidence
             │                   │
             └────── Smart Turn ─┘
                       │
                P(user_finished)
```

Pipecat Smart Turn is a particularly good fit because it operates directly on the **audio**, meaning “I… uh…” and “I.” don't look identical just because the text ending happens to be similar. It runs in as little as ~10 ms on some CPUs and approximately 65 ms in Pipecat's reported cloud testing. ([GitHub][7])

I'd begin evaluating EOT around **100–160 ms into silence**, rather than waiting 500 ms.

The decision becomes something like:

```python
if silence > 120ms:
    if smart_turn.p_finished > 0.92:
        commit_turn()

elif silence > adaptive_max_delay:
    commit_turn()
```

But `adaptive_max_delay` learns the person's speech cadence.

A fast-talking user might settle near 250 ms.

Someone who regularly pauses mid-sentence might settle near 450 ms.

That seemingly small feature makes the agent noticeably more courteous.

---

# Start thinking before the human finishes

This is probably the single biggest latency trick.

Imagine the partial transcript evolves:

```text
"can you compare our sales in the midwest..."

"can you compare our sales in the midwest this quarter..."

"can you compare our sales in the midwest this quarter versus last..."
```

Once ASR has a sufficiently stable prefix and the acoustic turn detector believes we're nearing completion, begin a **speculative generation**.

```text
USER AUDIO
───────────────────────────────┐
                               │ EOT
ASR ████████████████████████████
                  │
                  └─ stable prefix

LLM              ████████████████
                 speculative ^

TTS                                ███████
                                   committed only
```

If the user continues, kill the speculative generation.

If their final transcript is semantically equivalent, promote it.

If it materially changes, throw it away and restart.

You can do the same thing with **read-only retrieval**:

```text
"what was Domino's..."
       ↓
prefetch Domino's context

"...UK comp sales last quarter?"
       ↓
refine retrieval
```

Side-effecting tools never execute speculatively.

This can erase **100–300 ms** from perceived response latency.

---

# The LLM should write *speech*, not prose

Do not send normal assistant prose directly into the voice renderer.

The voice model should be prompted to produce something closer to:

```text
Interesting. The biggest change is actually in the Midwest.

Revenue is up about eight percent, but that's being driven mostly
by traffic rather than ticket size.

The part I'd watch is...
```

rather than:

```text
There are three key observations:

1. Midwest revenue increased...
2. Average ticket...
3. Traffic...
```

I would add a tiny **speech realization layer** after the reasoning model:

```text
LLM semantic stream
       │
       ▼
speech realization
  ├─ spoken punctuation
  ├─ number expansion
  ├─ abbreviations
  ├─ clause segmentation
  ├─ emphasis
  └─ occasional paralinguistic hints
       │
       ▼
TTS
```

But don't make this another big LLM call.

Mostly deterministic rules plus a small model when needed.

---

# Do not feed TTS token-by-token

That produces terrible prosody.

You need approximately **one syntactic clause of lookahead**, while aggressively minimizing first-chunk delay.

For example:

```text
LLM:

"That's actually a really good question, because the two architectures..."

TTS chunks:

1  "That's actually a really good question,"
2  "because the two architectures behave very differently."
```

Not:

```text
"That's actually"
"a really good"
"question because"
"the two"
"architectures..."
```

I'd target roughly:

**first chunk:** 3–7 words when acoustically safe
**subsequent chunks:** 6–18 words
**lookahead:** ~100–250 ms
**preferred break:** punctuation > syntax boundary > prosody prediction

CosyVoice's bi-streaming implementation is particularly advantageous here because the input itself can continue arriving while audio is being produced. ([GitHub][8])

---

# One very important interruption detail

Your LLM conversation history must reflect **what the user actually heard**, not what the LLM generated.

Suppose the model generated:

```text
There are actually three reasons. The first is labor costs.
The second is commodity inflation. The third...
```

but playback reached only:

```text
There are actually three reasons. The first—
```

when the user interrupted.

Your conversation history must contain approximately:

```text
assistant:
"There are actually three reasons. The first—"
```

not the entire generated response.

So every generated audio buffer gets timestamps linked back to word/clause boundaries:

```text
assistant_text
     ↓
word alignment
     ↓
generated audio
     ↓
RTP timestamps
     ↓
client playout ACK
```

On interruption:

```text
truncate assistant history
to last confirmed played word
```

This fixes a huge number of bizarre post-interruption behaviors.

---

# The latency budget I'd hold the team to

For a genuine 5/5 system, I'd make these release gates rather than aspirational metrics:

| Metric                             |                   5/5 target |
| ---------------------------------- | ---------------------------: |
| Mic packetization                  |                        20 ms |
| Local speech-onset detection       |                       <40 ms |
| Playback duck after user starts    |               **p50 <60 ms** |
| Hard barge-in stop                 | **p50 <120 ms, p95 <180 ms** |
| Streaming ASR chunk                |                    80–160 ms |
| ASR partial availability           |       <150 ms behind speaker |
| EOT decision after true ending     |           **p50 150–250 ms** |
| LLM first token                    |                <150 ms ideal |
| TTS first audio after text         |               **100–180 ms** |
| End-of-user → first audible AI     |           **p50 300–450 ms** |
| End-of-user → first audible AI p95 |                  **<650 ms** |
| False early EOT                    |                        <1–2% |
| Backchannel-induced cancellation   |                          <1% |
| Audible TTS splice defects         |             effectively zero |

The way you hit the 300–450 ms target isn't:

```text
200ms ASR
+ 200ms endpoint
+ 200ms LLM
+ 150ms TTS
= 750ms
```

It's overlapping them:

```text
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

# Databricks-specific topology

Databricks can own almost everything above the packet transport layer.

I would create:

```text
Databricks App
   realtime API / auth / session bootstrap
              │
              ▼
        Unity Gateway
              │
     ┌────────┼────────┐
     │        │        │
 GPT-OSS   Qwen       tools
 20B       Next       MCP/UC
     │        │        │
     └────────┼────────┘
              │
       streaming response
```

For an all-open inference path, my first bake-off would be:

**GPT-OSS-20B vs Qwen3-Next-80B-A3B-Instruct.**

Databricks currently exposes both GPT-OSS-20B/120B and Qwen3-Next through Foundation Model APIs, and GPT-OSS is also available via provisioned throughput. ([Databricks Documentation][9])

I would **not** make Qwen3.5-122B-A10B the default conversational path despite its quality; Databricks documents that model as reasoning-only and says reasoning cannot be disabled. That's exactly the wrong default latency characteristic for “yeah, what's the weather?” class conversational turns. ([Databricks Documentation][10])

For latency-critical production traffic, avoid scale-to-zero everywhere in this path. Databricks explicitly warns that GPU cold starts can take 10–20 seconds or even minutes, while route-optimized endpoints are intended for very low-latency workloads. ([Databricks Documentation][11])

Use MLflow to capture:

```text
voice_session
 ├── vad_onset
 ├── asr_partial_1
 ├── asr_final
 ├── eot_prediction
 ├── speculative_llm_start
 ├── llm_commit
 ├── llm_first_token
 ├── tts_submit
 ├── first_pcm
 ├── first_rtp
 ├── client_playout
 ├── barge_in
 ├── generation_cancel
 └── final_played_text
```

MLflow already supports streaming/async tracing for Databricks model calls, and production traces can land in Unity Catalog-backed storage. ([MLflow AI Platform][12])

That gives you an unusually powerful **voice quality observability system**, not merely application logging.

---

# Why I wouldn't just use Moshi

Moshi is fascinating because architecturally it is much closer to what ChatGPT Live represents: two simultaneous audio streams, continuous turn dynamics, an internal text stream, and practical latency reported around **200 ms on an L4**. ([GitHub][13])

I'd absolutely have a team running it.

But I would use it as:

```text
R&D lane / conversational-dynamics benchmark
```

rather than:

```text
primary enterprise assistant brain
```

because the cascade gives you dramatically more control over:

tool use, retrieval, deterministic business logic, governed model selection, transcript quality, safety, observability, model upgrades, grounding, and high-end reasoning.

The longer-term interesting architecture is:

```text
               DUPLEX AUDIO MODEL
              /                  \
 conversational                 acoustic
 behavior                       rendering
        \                         /
         \                       /
            semantic LLM brain
```

In other words, let an audio-native model learn **when to breathe, yield, overlap, acknowledge, pause, laugh, and interrupt**, while a stronger semantic model determines **what to say**.

That hybrid is where I think open systems eventually surpass today's cascade.

---

## My actual v1 build

If you told me we were starting Monday and needed the best result rather than the fewest components, I'd use:

**LiveKit OSS + Pipecat/custom controller → WebRTC AEC3 → TEN-VAD → Smart Turn v3 → Nemotron 3.5 Streaming ASR → Databricks GPT-OSS-20B/Qwen3-Next with speculative streaming → custom spoken-language chunker → CosyVoice 3 → WebRTC**, with Databricks Apps, Unity Gateway, UC/MCP, Lakebase/Delta and MLflow forming the surrounding application/control/observability plane. ([GitHub][14])

And I'd run three continuous challenger experiments:

```text
ASR:
Nemotron 3.5 80/160ms
vs
Parakeet Unified 240/320ms

TTS:
CosyVoice 3
vs
Chatterbox Turbo

conversation architecture:
cascade
vs
Moshi-derived full-duplex / hybrid controller
```

The biggest point is that **none of those individual model choices determines whether this gets a 5/5**. Nemotron + CosyVoice with mediocre orchestration will still feel like a voice bot.

The features that get you from **“excellent voice bot” to “this feels like ChatGPT Live”** are speculative turn execution, sub-100-ms perceived yielding, backchannel-vs-takeover discrimination, played-text-aware cancellation, acoustic endpointing, clause-aware streaming TTS, adaptive conversational cadence, and truly simultaneous listen/speak operation.

If I were allocating the engineering effort, I’d put **roughly half of it into that realtime controller and evaluation harness**, not the models themselves.

[1]: https://docs.databricks.com/aws/en/machine-learning/model-serving/route-optimization?utm_source=chatgpt.com "Route optimization on serving endpoints | Databricks on AWS"
[2]: https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b?utm_source=chatgpt.com "nvidia/nemotron-3.5-asr-streaming-0.6b · Hugging Face"
[3]: https://huggingface.co/nvidia/parakeet-unified-en-0.6b?utm_source=chatgpt.com "nvidia/parakeet-unified-en-0.6b · Hugging Face"
[4]: https://github.com/wehos/CosyVoice-v3?utm_source=chatgpt.com "GitHub - wehos/CosyVoice-v3: Multi-lingual large voice generation model, providing inference, training and deployment full-stack ability. · GitHub"
[5]: https://github.com/resemble-ai/chatterbox?utm_source=chatgpt.com "GitHub - resemble-ai/chatterbox: SoTA open-source TTS · GitHub"
[6]: https://github.com/TEN-framework/ten-vad?utm_source=chatgpt.com "GitHub - TEN-framework/ten-vad: Voice Activity Detector (VAD) : low-latency, high-performance and lightweight · GitHub"
[7]: https://github.com/pipecat-ai/smart-turn/blob/main/README.md?utm_source=chatgpt.com "smart-turn/README.md at main · pipecat-ai/smart-turn · GitHub"
[8]: https://github.com/wehos/CosyVoice-v3/blob/main/README.md?utm_source=chatgpt.com "CosyVoice-v3/README.md at main · wehos/CosyVoice-v3 · GitHub"
[9]: https://docs.databricks.com/aws/en/machine-learning/model-serving/foundation-model-overview?utm_source=chatgpt.com "Supported foundation models on Model Serving | Databricks on AWS"
[10]: https://docs.databricks.com/gcp/en/machine-learning/foundation-model-apis/supported-models?utm_source=chatgpt.com "Databricks-hosted foundation models available in Foundation Model APIs | Databricks on Google Cloud"
[11]: https://docs.databricks.com/gcp/en/machine-learning/model-serving/custom-models?utm_source=chatgpt.com "Custom models overview | Databricks on Google Cloud"
[12]: https://www.mlflow.org/docs/latest/genai/tracing/integrations/listing/databricks/?utm_source=chatgpt.com "Tracing Databricks | MLflow AI Platform"
[13]: https://github.com/realharry/kyutai-moshi?utm_source=chatgpt.com "GitHub - realharry/kyutai-moshi: Moshi is a speech-text foundation model and full-duplex spoken dialogue framework. It uses Mimi, a state-of-the-art streaming neural audio codec. · GitHub"
[14]: https://github.com/InterScribe/livekit-agents?utm_source=chatgpt.com "GitHub - InterScribe/livekit-agents: Build real-time multimodal AI applications 🤖🎙️📹 · GitHub"
