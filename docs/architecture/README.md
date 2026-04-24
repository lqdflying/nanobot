# nanobot — Architecture Overview

## What is nanobot

nanobot is a multi-channel AI agent framework that routes messages from various chat platforms (Telegram, Discord, WeChat, Feishu, etc.) through a unified agent processing engine, then back to the originating channel.

## Tech Stack

| Layer | Technology | Role |
|-------|-----------|------|
| Agent engine | Python, asyncio | Core reasoning, tool orchestration |
| LLM providers | OpenAI, Anthropic, Google, Azure, OpenRouter, local | Model abstraction |
| Channels | Telegram Bot API, Discord API, WeChat Work, Feishu, Matrix, etc. | Platform adapters |
| Session storage | JSON files (`~/.openclaw/sessions/`) | Conversation persistence |
| Cron scheduling | APScheduler | Background task scheduling |
| Bus | `nanobot.bus` (in-process queue) | Internal event routing |

## System Layers

```
┌─────────────────────────────────────────────────────┐
│  Channels                                             │
│  telegram | discord | feishu | weixin | email | ...│
└──────────────────┬──────────────────────────────────┘
                   │ InboundMessage / OutboundMessage
┌──────────────────▼──────────────────────────────────┐
│  Bus (MessageBus)                                     │
│  queue.py — in-process pub/sub                       │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│  AgentLoop (loop.py)                                 │
│  process_direct() — orchestrates the full turn      │
│    │                                                │
│    ├─ ContextBuilder — builds LLM prompt            │
│    ├─ AgentRunner — runs LLM + tools loop            │
│    ├─ Consolidator — summarises history on demand   │
│    └─ ToolRegistry — resolves and dispatches tools   │
└──────────────────┬──────────────────────────────────┘
                   │
         ┌─────────▼─────────┐
         │  LLM Provider     │
         │  (OpenAI / etc.)  │
         └─────────┬─────────┘
                   │
         ┌─────────▼─────────┐
         │  Tools             │
         │  cron | web | shell | spawn | ... │
         └────────────────────┘
```

## Module Map

| Module | Responsibility |
|--------|----------------|
| `agent/loop.py` | Core processing engine — `process_direct()` |
| `agent/runner.py` | LLM call loop — runs model + tools until done |
| `agent/memory.py` | Consolidator — summarises history; Dream module |
| `agent/tools/` | All built-in tools (cron, web, shell, search, file, etc.) |
| `channels/` | Per-platform channel adapters (15 channels) |
| `api/server.py` | HTTP REST API + WebSocket |
| `bus/` | Internal event bus (queue-based pub/sub) |
| `cron/` | CronService — background job scheduling |
| `session/` | Session persistence (JSON files) |
| `config/` | Config schema + loader + env resolution |
| `providers/` | LLM provider abstraction layer |
| `cli/` | CLI commands (onboard, compact, auth, etc.) |
| `command/` | Text command routing (e.g. `/compact`) |

## Key Entry Points

### Channel Inbound
```
Channel adapter (e.g. telegram.py)
  → MessageBus.queue.put(InboundMessage)
  → AgentLoop.process_direct()   ← main entry
```

### HTTP API
```
api/server.py
  → POST /v1/chat/completions (OpenAI-compatible)
  → AgentLoop.process_direct() via Nanobot facade
```

### Cron Callback
```
CronService fires scheduled job
  → CronTool._run_job()
  → AgentLoop.process_direct() with cron context
  → delivers result to channel
```

## Related Documentation

- [System Architecture](./nanobot-arch.md)
- [Data Flow](./data-flow.md)
- [Agent Loop](../nanobot/agent/doc/loop.md)
- [Memory System](../nanobot/agent/doc/memory.md)
- [Channel Architecture](../nanobot/channels/doc/README.md)
- [Cron Tool](../docs/cron-tool.md)
- [Session Management](../docs/memory.md)
