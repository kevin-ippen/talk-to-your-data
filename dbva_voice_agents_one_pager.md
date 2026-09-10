# One-Pager: DBVA (Databricks Voice Agents)

## Executive Summary

Databricks Voice Agents (DBVA) is a real-time voice agent framework integrated into Agent Bricks and packaged as a reusable Databricks App template. Developed by Central Engineering, DBVA delivers full-duplex, bidirectional voice interactions with sub-second latency. It leverages the OpenAI Realtime API over streaming WebSockets, allowing AI voice agents to listen, speak, interrupt naturally, and execute arbitrary tool calls on Databricks.

## Key Assets and Repository Links

* GitHub Repository (Databricks App Template): [smurching/voice-agent](https://github.com/smurching/voice-agent)
* Internal Pull Request (Agent Bricks Core): [databricks-eng/universe/pull/1723005](https://github.com/databricks-eng/universe/pull/1723005)
* Internal Documentation: [DBVA Confluence Page](https://databricks.atlassian.net/wiki/spaces/UN/pages/6121619615)

## Project Ownership

* Primary Owners / Engineering Leads: Sabhya Chhabria (Engineering - Central) and Siddharth Murching (Engineering - Central)

## Problem Statement

Traditional conversational AI voice architectures rely on cascaded pipelines: recording voice segments, executing speech-to-text, prompting a large language model, and running text-to-speech. While functional, cascaded chains introduce latency of 2 to 5 seconds per turn and cannot easily handle natural human interruptions, conversational pauses, or backchannel feedback. To achieve a responsive ChatGPT-style voice experience, voice agents require native speech-to-speech streaming with concurrent tool execution.

## Architecture and Technical Implementation

DBVA provides an end-to-end framework consisting of three main layers:

1. Streaming Voice Transport Layer:
* Powered by the OpenAI Realtime API using persistent WebSocket connections.
* Streams bidirectional audio chunks between the browser frontend and the audio model, eliminating chunk-buffering delays and enabling instantaneous conversational barge-in / interruption.

2. Agent Bricks Integration:
* Integrated into the core Databricks Agent Bricks platform via PR 1723005.
* Provides configuration mechanisms for agent personas, speech characteristics, prompt instructions, and function schemas.

3. Real-Time Tool Calling Engine:
* Agents can invoke Python functions, external APIs, and Databricks endpoints during live speech.
* For data queries, the agent can issue tool calls directly against Databricks Genie spaces, executing SQL or conversational analytics while keeping the voice stream active.

4. Databricks App Delivery:
* Delivered as a standardized template in the smurching/voice-agent repository.
* Allows developers and solution architects to deploy a customized voice agent web application onto Databricks Apps with minimal configuration.

## Key Capabilities and Differentiators

* ChatGPT-Grade Latency: Sub-second audio turnaround delivers true conversational rhythm and flow.
* Full-Duplex Interruption: Users can speak over the agent at any point; the WebSocket stream immediately halts audio generation and listens.
* Extensible Tooling: Functions can query Unity Catalog tables, invoke Genie spaces, trigger Databricks Workflows, or interface with internal services.
* Fast Deployment on Databricks Apps: Enables rapid prototyping and hosting of bespoke voice agents inside governed enterprise workspaces.
