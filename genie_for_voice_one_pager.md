# One-Pager: Genie for Voice (Genie-Powered Realtime Voice Interface)

## Executive Summary

Genie for Voice is a Databricks-native, real-time voice interface designed to enable hands-free conversational analytics and customer-360 visibility. Developed under the Field Engineering Innovation Program (FEIP-7941), the solution pairs open-source speech models hosted on Databricks Model Serving with Genie, Genie Agent Mode, and Lakebase. It is delivered as an active Databricks App with built-in multi-lingual capabilities, streaming latency testing, and Model Context Protocol (MCP) server support.

## Key Assets and Repository Links

* GitHub Repository: [suneelsunkara-db/genie-voice-agent](https://github.com/suneelsunkara-db/genie-voice-agent)
* Live Databricks App: [genie-voice-agent App](https://genie-voice-agent-3644297589119053.aws.databricksapps.com/) (Internal Shortlink: [go/genie-for-voice](http://go/genie-for-voice))
* Realtime Latency Testing Console: [genie-voice-agent Latency Test](https://genie-voice-agent-3644297589119053.aws.databricksapps.com/realtime-test/)
* Realtime API Endpoint: [genie-voice-agent Realtime Endpoint](https://genie-voice-agent-3644297589119053.aws.databricksapps.com/realtime/)
* MCP Server Endpoint: [genie-voice-agent MCP Server](https://genie-voice-agent-3644297589119053.aws.databricksapps.com/realtime/mcp)
* Jira Epic: [FEIP-7941: Genie For Voice](https://databricks.atlassian.net/browse/FEIP-7941)
* Pitch Deck: [Genie for Voice Google Presentation](https://docs.google.com/presentation/d/1YSLhfS6QO_U0fAIsOGwcitk2dU43tJWFOWUCQzshXSQ/edit?slide=id.g3ef1c592a17_0_384#slide=id.g3ef1c592a17_0_384)
* Video Demo (General): [Google Drive Demo Video](https://drive.google.com/file/d/1wWxTXcpvkyBAJ3Ce7x2iT4PvhZYU6jiG/view?usp=drive_link)
* Video Demo (Thai): [Google Drive Thai Language Demo](https://drive.google.com/file/d/1cqOaVjv6yWQQzs8pBW5UAP5QIZFywiNQ/view?usp=drive_link)

## Project Ownership

* Primary Owner: Suneel Sunkara (Specialist Solutions Architect, GitHub: suneelsunkara-db)
* Executive Sponsor: Takehisa Ueda (Field Demo Excellence Management)
* Advisory and Review Team: Kyle Hale, Quentin Ambard, Cal Reynolds, Dillon Bostwick

## Problem Statement

Standard speech-to-text (STT) and voice transcription engines convert spoken conversations into text, but they lack real-time enterprise business context, including customer billing history, outstanding disputes, invoices, and product telemetry. In contact-center and field operations, agents must manually pivot between audio streams and static dashboards, resulting in elevated average handle time (AHT) and inconsistent answers. External cloud voice solutions often require exporting sensitive customer conversation data outside corporate boundaries.

## Architecture and Technical Implementation

The architecture implements a self-contained, Databricks-hosted pipeline centered around three core APIs:

1. Speech-to-Text (STT) API:
* Uses an open-source Whisper model fine-tuned via LoRA (model endpoint: voice_asr_en_finetuned_whisper_lora).
* Deployed directly onto Databricks Model Serving for low-latency, governed transcription without third-party API dependencies.

2. Speech-LLM Agent Layer (Tool-Calling to Genie):
* Binds live transcribed text directly into Databricks Genie and Genie Agent Mode.
* Leverages Genie Ontology, Lakebase, and underlying Delta tables to evaluate business schemas, synthesize SQL queries, and return grounded analytical context.

3. Text-to-Speech (TTS) API:
* Synthesizes audio responses using open-source TTS models deployed on Databricks Model Serving.
* Delivers natural spoken feedback back to the client interface.

4. Client Interface and Protocol:
* Hosted as a Databricks App with a real-time web UI tailored for contact-center agent assistance and executive Q&A.
* Provides an embedded Model Context Protocol (MCP) server for integration with agent tooling and external orchestrators.

## Key Capabilities and Differentiators

* Multilingual Support: Built-in support for 22 languages with automated transcription and context mapping.
* Grounded Enterprise Context: Query results are executed against Unity Catalog-governed tables rather than relying on generative hallucination.
* Total Data Governance: Speech models, LLM routing, and SQL execution operate entirely within the customer Databricks environment.
* Lower Token Economics: Combining local open-source STT/TTS with targeted Genie API calls provides substantial cost savings compared to external voice platforms.
