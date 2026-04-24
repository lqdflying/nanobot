# Tools Overview

The nanobot agent is extended through a **plugin-style tool system** built on JSON Schema validation, a central registry, and an async execution model.

---

## Architecture

```mermaid
flowchart LR
    subgraph AgentLoop
        L[("Agent Loop")]
    end

    subgraph ToolRegistry
        R[("ToolRegistry")]
        T1[("Tool A")]
        T2[("Tool B")]
        T3[("Tool N")]
    end

    subgraph Tools[" "]
        direction TB
        B[("Tool (ABC)")]
        SP[("@tool_parameters decorator")]
        S[("Schema classes")]
    end

    L --> R
    R --> T1
    R --> T2
    R --> T3
    T1 --> B
    T2 --> B
    T3 --> B
    T1 --> SP
    T2 --> SP
    SP -. attach .- B
    B -. implements .- S
```

- **`Tool`** (ABC) — every tool inherits from this and implements `name`, `description`, `parameters`, and `execute()`.
- **`@tool_parameters`** — class decorator that attaches a JSON Schema to `parameters` without boilerplate.
- **`Schema`** — abstract base for typed schema fragments (`StringSchema`, `IntegerSchema`, etc.).
- **`ToolRegistry`** — single source of truth: registers tools, dispatches calls, validates, and caches definitions.

---

## Tool Base Class

```mermaid
classDiagram
    direction LR

    class Schema {
        <<abstract>>
        +to_json_schema() dict
        +validate_value(Any, str) list
        +validate_json_schema_value(Any, dict, str) list
        +fragment(Any) dict
        +resolve_json_schema_type(Any) str
    }

    class Tool {
        <<abstract>>
        +name: str
        +description: str
        +parameters: dict
        +read_only: bool
        +concurrency_safe: bool
        +exclusive: bool
        +execute(**kwargs) Any
        +cast_params(dict) dict
        +validate_params(dict) list
        +to_schema() dict
    }

    class tool_parameters {
        <<decorator>>
        Attaches JSON Schema to Tool.parameters
    }

    Tool --> Schema : uses
    Tool ..> tool_parameters : decorated by
```

### Tool Lifecycle in the Agent Loop

```mermaid
sequenceDiagram
    participant LLM
    participant Registry as ToolRegistry
    participant Tool

    LLM->>Registry: tool_calls = [ {name, parameters} ]
    Loop For each call
        Registry->>Registry: prepare_call(name, params)
        Note over Registry: cast_params() → validate_params()
        Registry->>Tool: execute(**params)
        Tool-->>Registry: result (str or list)
        Registry-->>LLM: tool_result
    End
```

---

## Schema System

All schema types subclass `Schema` and implement `to_json_schema()`.

```mermaid
classDiagram
    direction TB

    class Schema {
        <<abstract>>
        +to_json_schema() dict
        +validate_value(Any, str) list
    }

    class StringSchema {
        +description str
        +min_length int?
        +max_length int?
        +enum tuple?
        +nullable bool
    }

    class IntegerSchema {
        +minimum int?
        +maximum int?
        +enum tuple?
        +nullable bool
    }

    class NumberSchema {
        +minimum float?
        +maximum float?
        +enum tuple?
        +nullable bool
    }

    class BooleanSchema {
        +default bool?
        +nullable bool
    }

    class ArraySchema {
        +items Schema
        +min_items int?
        +max_items int?
        +nullable bool
    }

    class ObjectSchema {
        +properties dict
        +required list
        +additional_properties bool?
        +nullable bool
    }

    Schema <|-- StringSchema
    Schema <|-- IntegerSchema
    Schema <|-- NumberSchema
    Schema <|-- BooleanSchema
    Schema <|-- ArraySchema
    Schema <|-- ObjectSchema
```

### Schema → JSON Schema Fragment

```mermaid
flowchart LR
    S["StringSchema(description='Path', max_length=5000)"]
    I["IntegerSchema(0, minimum=1, maximum=600)"]
    B["BooleanSchema(description='deliver')"]

    S -->|to_json_schema| JS1["{type:'string', description:'Path', maxLength:5000}"]
    I -->|to_json_schema| JS2["{type:'integer', minimum:1, maximum:600}"]
    B -->|to_json_schema| JS3["{type:'boolean', description:'deliver'}"]
```

---

## Built-in Tools

| Tool Name | File | Purpose |
|-----------|------|---------|
| `my` | `self.py` | Inspect and mutate the agent loop's runtime state (scratchpad, config, iteration) |
| `cron` | `cron.py` | Schedule one-time or recurring tasks with cron expressions or intervals |
| `exec` | `shell.py` | Execute shell commands with sandboxing, timeout, and env filtering |
| `web_search` | `web.py` | Search the web via Brave, DuckDuckGo, Tavily, SearXNG, Jina, or Kagi |
| `web_fetch` | `web.py` | Fetch a URL and extract readable content (markdown/text) |
| `grep` | `search.py` | Search file contents with regex, context lines, and glob filters |
| `glob` | `search.py` | Find files by glob patterns (py, md, json, etc.) |
| `read_file` | `filesystem.py` | Read file contents with optional offset/limit |
| `write_file` | `filesystem.py` | Write content to a file (creates or overwrites) |
| `edit_file` | `filesystem.py` | Apply targeted text replacements to a file |
| `list_dir` | `filesystem.py` | List directory contents |
| `make_dir` | `filesystem.py` | Create a directory |
| `remove_path` | `filesystem.py` | Remove a file or directory (recoverable via trash) |
| `move_path` | `filesystem.py` | Move/rename a file or directory |
| `copy_path` | `filesystem.py` | Copy a file |
| `path_info` | `filesystem.py` | Get file metadata (size, mtime, is_dir) |
| `spawn` | `spawn.py` | Spawn a sub-agent session |
| `message` | `message.py` | Send messages to external channels (Telegram, WeChat, Discord, etc.) |
| `file_state` | `file_state.py` | Track and persist per-file operation state across turns |
| `notebook` | `notebook.py` | Execute cells in a Jupyter-style notebook |
| `mcp_*` | `mcp.py` | MCP server tools (dynamic, prefixed with `mcp_`) |

---

## ToolRegistry Flow

```mermaid
flowchart TD
    Register["register(tool: Tool)"]
    Unregister["unregister(name: str)"]
    Prepare["prepare_call(name, params)"]
    Execute["execute(name, params)"]
    Defs["get_definitions()"]

    Register --> R1["_cached_definitions = None"]
    Unregister --> U1["_cached_definitions = None"]
    Defs --> D1{"_cached_definitions\n!= None?"}
    D1 -->|Yes| D2["return cache"]
    D1 -->|No| D3["builtins sorted + mcp_ sorted"]
    D3 --> D4["cache and return"]
    Prepare --> P1{"tool exists?"}
    P1 -->|No| P2["Error: not found"]
    P1 -->|Yes| P3["cast_params()"]
    P3 --> P4["validate_params()"]
    P4 --> P5{"errors?"}
    P5 -->|Yes| P6["Error: invalid params"]
    P5 -->|No| P7["(tool, cast_params, None)"]
    Execute --> E1{"error from prepare?"}
    E1 -->|Yes| E2["return error + hint"]
    E1 -->|No| E3["tool.execute(**params)"]
    E3 --> E4{"raises?"}
    E4 -->|Yes| E5["Error: {e}"]
    E4 -->|No| E6["return result"]
```
