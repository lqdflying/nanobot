# Agent Module

The agent module is the core processing engine of nanobot. It receives messages, builds context, calls the LLM, executes tools, and sends responses.

## Sub-Modules

| Module | File | Description |
|--------|------|-------------|
| **loop** | `agent/loop.py` | `AgentLoop` — the main entry point. Receives `InboundMessage`, orchestrates the full turn including context building, LLM calls, tool execution, session saving, and consolidation. |
| **runner** | `agent/runner.py` | `AgentRunner` — the core tool-calling loop. Runs LLM → tool calls → results → repeat until done. Handles context governance (snipping, micro-compaction), injection detection, and checkpointing. |
| **memory** | `agent/memory.py` | `MemoryStore`, `Consolidator`, `Dream` — pure file I/O store, lightweight token-budget triggered consolidation, and heavyweight cron-scheduled memory consolidation. |
| **context** | `agent/context.py` | `ContextBuilder` — builds the system prompt and messages from history, memory, bootstrap files, and skills. |
| **hook** | `agent/hook.py` | `AgentHook`, `AgentHookContext`, `CompositeHook` — lifecycle hooks for runner customization (streaming, progress, before/after iteration). |
| **autocompact** | `agent/autocompact.py` | `AutoCompact` — proactive compression of idle sessions to reduce token cost and latency. |
| **subagent** | `agent/subagent.py` | `SubagentManager`, `SubagentStatus` — manages background task execution via spawned subagents. |
| **skills** | `agent/skills.py` | `SkillsLoader`, `BUILTIN_SKILLS_DIR` — loads agent skills from `SKILL.md` files (workspace and builtin). |

## Tools

All tools inherit from `agent/tools/base.py` → `Tool` and are registered in `agent/tools/registry.py` → `ToolRegistry`.

| Tool | File | Description |
|------|------|-------------|
| **base** | `agent/tools/base.py` | `Tool` abstract base class, `Schema` ABC for JSON Schema validation. Core interface: `name`, `description`, `parameters`, `execute()` |
| **cron** | `agent/tools/cron.py` | `CronTool` — schedule and manage cron jobs. |
| **file_state** | `agent/tools/file_state.py` | `FileStateTool` — track file modification state across turns. |
| **filesystem** | `agent/tools/filesystem.py` | `ReadFileTool`, `WriteFileTool`, `EditFileTool`, `ListDirTool` — file operations with workspace restrictions. |
| **message** | `agent/tools/message.py` | `MessageTool` — send outbound messages via the message bus. |
| **mcp** | `agent/tools/mcp.py` | MCP (Model Context Protocol) client tool — calls external MCP servers. |
| **notebook** | `agent/tools/notebook.py` | `NotebookEditTool` — Jupyter notebook cell editing. |
| **registry** | `agent/tools/registry.py` | `ToolRegistry` — dynamic tool registration and execution. |
| **sandbox** | `agent/tools/sandbox.py` | `SandboxTool` — isolated execution environment. |
| **schema** | `agent/tools/schema.py` | Concrete `Schema` implementations: `StringSchema`, `IntegerSchema`, `BooleanSchema`, etc. |
| **search** | `agent/tools/search.py` | `GlobTool`, `GrepTool` — file search and content search. |
| **self** | `agent/tools/self.py` | `MyTool` — runtime state inspection and configuration for the agent loop. Blocked attributes (core infrastructure, config, credentials). |
| **shell** | `agent/tools/shell.py` | `ExecTool` — shell command execution with workspace restrictions and sandbox support. |
| **spawn** | `agent/tools/spawn.py` | `SpawnTool` — spawn subagent background tasks. |
| **web** | `agent/tools/web.py` | `WebSearchTool`, `WebFetchTool` — web search and fetch with proxy support. |

## Key Classes

### AgentLoop (`agent/loop.py`)

The main entry point for processing. Receives `InboundMessage` from the bus, orchestrates the full turn:

```python
class AgentLoop:
    def __init__(self, bus, provider, workspace, ...)
    async def run() -> None              # Main loop: consume inbound, dispatch tasks
    async def process_direct(...) -> OutboundMessage | None  # Main entry point
    async def _process_message(...) -> OutboundMessage | None  # Per-message processing
    async def _run_agent_loop(...) -> tuple   # LLM + tools until done
    def _save_turn(...) -> None          # Persist turn to session
    async def maybe_consolidate_by_tokens(session)  # Delegates to Consolidator
```

### AgentRunner (`agent/runner.py`)

The core tool-calling loop. Stateless, shared by `AgentLoop` and `Dream`.

```python
class AgentRunner:
    def __init__(self, provider: LLMProvider)
    async def run(self, spec: AgentRunSpec) -> AgentRunResult

@dataclass
class AgentRunSpec:
    initial_messages: list[dict[str, Any]]
    tools: ToolRegistry
    model: str
    max_iterations: int
    max_tool_result_chars: int
    # ... hook, checkpoint_callback, injection_callback, etc.
```

### Consolidator (`agent/memory.py`)

Lightweight token-budget triggered consolidation. Summarizes evicted messages into `history.jsonl` via LLM.

```python
class Consolidator:
    async def maybe_consolidate_by_tokens(session: Session) -> None
    async def archive(messages: list[dict]) -> str | None  # LLM summarization
    def pick_consolidation_boundary(...) -> tuple[int, int] | None
```

### Dream (`agent/memory.py`)

Heavyweight cron-scheduled memory consolidation. Two-phase: (1) LLM analysis, (2) `AgentRunner` with `read_file`/`edit_file` to make targeted edits.

```python
class Dream:
    async def run() -> bool  # Returns True if work was done
```

### ToolRegistry (`agent/tools/registry.py`)

Dynamic tool management.

```python
class ToolRegistry:
    def register(self, tool: Tool) -> None
    def get(self, name: str) -> Tool | None
    def get_definitions() -> list[dict[str, Any]]
    def execute(name, params) -> Any
```

### MyTool (`agent/tools/self.py`)

Runtime introspection tool. Allows the agent to inspect and configure its own runtime state.

```python
class MyTool(Tool):
    BLOCKED = frozenset({...})   # Cannot inspect or modify
    READ_ONLY = frozenset({...}) # Inspect OK, modify blocked
```