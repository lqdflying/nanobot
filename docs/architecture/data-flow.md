# Data Flow

## Full Turn: Inbound to Outbound

```mermaid
flowchart LR
    %% Entry points
    subgraph inbound["Entry Points"]
        T[User sends<br/>message on<br/>Telegram]
        D[User sends<br/>message on<br/>Discord]
        F[User sends<br/>message on<br/>Feishu]
        W[User sends<br/>message on<br/>WeChat]
        HTTP[HTTP POST<br/>/v1/chat/completions]
        CRON[Cron job<br/>fires]
    end

    %% Channel adapters
    subgraph channels["Channel Adapters (channels/)"]
        TA[telegram.py]
        DA[discord.py]
        FA[feishu.py]
        WA[weixin.py]
        API[api/server.py]
    end

    %% Message bus
    BUS[(MessageBus<br/>bus/queue.py)]

    %% Agent
    subgraph agent["AgentLoop (agent/loop.py)"]
        P1[1. Persist user<br/>message early]
        P2[2. ContextBuilder<br/>builds prompt]
        P3[3. AgentRunner<br/>LLM + tools loop]
        P4[4. Save turn<br/>to session]
        P5[5. Consolidator<br/>if needed]
    end

    %% LLM
    LLM[(LLM Provider)]

    %% Tools
    subgraph tools["Tools (agent/tools/)"]
        CRON_T[✓ cron]
        SEARCH_T[✓ search<br/>grep/glob]
        FILE_T[✓ file<br/>read/write/edit]
        WEB_T[✓ web<br/>search/fetch]
        SHELL_T[✓ shell/exec]
        SELF_T[✓ my/check-set]
        SPAWN_T[✓ spawn]
        SANDBOX_T[✓ sandbox]
        MSG_T[✓ message]
        MCP_T[✓ mcp]
    end

    %% Session
    SESSION[(Session<br/>Manager<br/>session/)]

    %% Outbound
    OUT[OutboundMessage]
    DELIVER[Channel<br/>delivers<br/>to user]

    T & D & F & W --> TA & DA & FA & WA
    HTTP --> API
    CRON --> P1
    TA & DA & FA & WA & API --> BUS
    BUS --> P1
    P1 --> SESSION
    P2 --> LLM
    LLM --> P3
    P3 --> SEARCH_T & FILE_T & WEB_T & SHELL_T & SELF_T & SPAWN_T & SANDBOX_T & MSG_T & MCP_T & CRON_T
    SEARCH_T & FILE_T & WEB_T & SHELL_T & SELF_T & SPAWN_T & SANDBOX_T & MSG_T & MCP_T & CRON_T --> P3
    P3 --> P4
    P4 --> SESSION
    P5 --> SESSION
    P4 --> OUT
    OUT --> DELIVER
```

## Channel Adapter Pattern

Every channel adapter follows the same pattern:

```python
# Pseudo-code for all channel adapters
class SomeChannel:
    async def handle_inbound(self, message: dict) -> None:
        """Convert platform-specific message → InboundMessage → bus."""
        inbound = InboundMessage(
            role="user",
            content=message["text"],
            channel="some_channel",
            chat_id=message["chat_id"],
            metadata={...},
        )
        await self.bus.queue.put(inbound)

    async def handle_outbound(self, outbound: OutboundMessage) -> None:
        """Convert OutboundMessage → platform-specific format → send."""
        await self.api.send_text(
            chat_id=outbound.chat_id,
            text=outbound.content,
        )
```

## Session Persistence Flow

```mermaid
sequenceDiagram
    participant U as User
    participant L as AgentLoop
    participant S as SessionManager
    participant FS as JSON Files<br/>(~/.openclaw/sessions/)
    participant MEM as Memory/<br/>Consolidator
    participant LLM as LLM

    Note over U,S: Turn N begins
    U->>L: User message
    L->>S: add_message("user", ...)
    L->>S: save() — persist user before agent runs
    L->>S: _mark_pending_user_turn()

    rect rgb(20, 40, 20)
        Note over L,LLM: Agent runs
        L->>L: _run_agent_loop()
        loop Tool calls
            L->>LLM: chat completions
            LLM-->>L: response
            L->>S: tool calls execute
        end
    end

    L->>S: _save_turn(messages, skip=N)
    L->>S: save() — persist full turn
    S->>FS: write session JSON

    rect rgb(20, 40, 20)
        Note over MEM,LLM: Consolidation check
        MEM->>MEM: maybe_consolidate_by_tokens()
        alt session too large
            MEM->>LLM: summarise(messages)
            LLM-->>MEM: summary
            MEM->>S: store summary
            S->>FS: update session JSON
        else LLM error
            MEM->>MEM: raw_archive(messages)
            S->>FS: raw dump
        end
    end

    Note over U,S: Turn N ends, Turn N+1 begins
```

## Cron Flow

```mermaid
flowchart TD
    CRON_JOB[Cron job fires<br/>at scheduled time]
    CS[CronService]
    CT[CronTool._run_job]
    BUS[(MessageBus)]

    CRON_JOB --> CS
    CS --> CT
    CT --> BUS
    BUS --> LOOP[AgentLoop<br/>process_direct]
    LOOP --> RESULT[Result content]
    RESULT --> DELIVER[Deliver to<br/>channel/chat]

    CT --> CRON_JOB2[Schedule next run<br/>(if recurring)]
```

## Config Flow

```mermaid
flowchart LR
    CFG[config.json<br/>or env vars]
    LOAD[config/loader.py<br/>load_config]
    RESOLVE[resolve_config_env_vars]
    SCHEMA[config/schema.py<br/>Config model]
    AGENT[AgentLoop<br/>from_config]

    CFG --> LOAD
    LOAD --> RESOLVE
    RESOLVE --> SCHEMA
    SCHEMA --> AGENT
```

## Subagent Flow

```mermaid
flowchart TD
    MAIN[Main AgentLoop<br/>process_direct]
    SAM[SubagentManager]
    SUB_LOOP[Sub-agent<br/>AgentLoop]
    SUB_RUNNER[AgentRunner]
    LLM[(LLM)]

    MAIN --> SAM
    SAM --> SUB_LOOP
    SUB_LOOP --> SUB_RUNNER
    SUB_RUNNER --> LLM
    LLM --> SUB_RUNNER
    SUB_RUNNER --> RESULT[Result returned<br/>to main agent]
    RESULT --> MAIN
```
