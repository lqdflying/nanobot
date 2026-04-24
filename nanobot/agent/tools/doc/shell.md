# ExecTool — Shell Command Execution

**File:** `tools/shell.py`
**Tool name:** `exec`

Executes arbitrary shell commands in a subprocess with timeout enforcement, environment variable filtering, optional sandboxing, and workspace restriction.

---

## Execution Flow

```mermaid
flowchart TD
    E["exec(command, working_dir?, timeout?)"]

    E --> W{"restrict_to_workspace?"}
    W -->|Yes| W1["resolve working_dir\n vs workspace_root"]
    W1 -->|outside| WE["Error: working_dir\noutside workspace"]
    W1 -->|inside| G
    W -->|No| G

    G["_guard_command()"]
    G --> GP["deny_patterns match?"]
    GP -->|Match| GE["Error: blocked\n(dangerous pattern)"]
    GP -->|No| GA["allow_patterns set?"]
    GA -->|Yes| GAA["in allowlist?"]
    GAA -->|No| GEA["Error: not in allowlist"]
    GA -->|No| GI["internal URL check"]
    GAA -->|Yes| GI
    GI -->|"fail"| GIU["Error: internal URL"]
    GI -->|"pass"| SANDA

    SANDA{"sandbox set?"}
    SANDA -->|Yes| SA["wrap_command(sandbox, cmd, workspace, cwd)"]
    SANDA -->|No| SPA
    SA --> SPA["PATH append if set"]
    SPA --> SP["_build_env()"]
    SP --> SPA2["spawn subprocess\n (bash -l -c or cmd /c)"]
    SPA2 --> RUN["asyncio.wait_for\n(communicate, timeout)"]
    RUN -->|timeout| K["_kill_process()\nTimeoutError → return error"]
    RUN -->|success| DEC["decode stdout/stderr\n(utf-8, replace errors)"]
    DEC --> TR["truncate at 10 000 chars\n(half+half pattern)"]
    TR --> RES["return result"]
```

---

## Environment Variable Filtering

```mermaid
flowchart LR
    ENV["os.environ"]

    subgraph Unix["Unix (bash -l)"]
        U1["HOME, LANG, TERM\n always passed"]
        U2["allowed_env_keys\n forwarded if set"]
    end

    subgraph Windows["Windows (cmd /c)"]
        W1["SYSTEMROOT, COMSPEC,\nUSERPROFILE, HOMEDRIVE,\nHOMEPATH, TEMP, TMP,\nPATH, PATHEXT, APPDATA,\nLOCALAPPDATA, ProgramData..."]
        W2["allowed_env_keys\n forwarded if set"]
    end

    ENV --> U1
    ENV --> U2
    ENV --> W1
    ENV --> W2
```

### `allowed_env_keys`

```python
# Only these keys from os.environ are forwarded to the subprocess
allowed_env_keys: list[str] = ["OPENAI_API_KEY", "BRAVE_API_KEY", "MY_CUSTOM_VAR"]
```

- On **Unix**: Only `HOME`, `LANG`, `TERM` are unconditionally forwarded. Login shell (`bash -l`) populates `PATH` and other essentials via profile. Additional keys from `allowed_env_keys` are merged in.
- On **Windows**: A curated set of system variables is forwarded unconditionally. Additional keys from `allowed_env_keys` are merged in.
- **All secrets** (API keys, tokens, passwords) are **never forwarded** unless explicitly named in `allowed_env_keys`.

---

## Sandbox Integration

```mermaid
flowchart LR
    SB["sandbox = \"bwrap\" | \"proot\" | \"nspawn\"..."]
    SB --> W["wrap_command(sandbox, command, workspace, cwd)"]
    W -->|"bwrap"| B["bwrap --dev --proc /proc \\\n  --bind workspace / \\\n  --tmpfs /tmp \\\n  command"]
    W -->|"proot"| P["proot -r workspace \\\n  -b /proc \\\n  command"]
    W -->|"nspawn"| N["systemd-nspawn --register=no \\\n  --bind =/proc \\\n  -D workspace \\\n  command"]
```

Sandboxing is only applied on **Unix**. On Windows, a warning is logged and the command runs unsandboxed.

---

## Safety Guards

```mermaid
flowchart TD
    DG["Deny Patterns\n(regex)"]
    AG["Allow Patterns\n(regex, optional)"]

    DG --> DM1["\\brm\\s+-[rf]{1,2}\\b  → rm -r, rm -rf"]
    DG --> DM2["\\bdel\\s+/[fq]\\b  → del /f, del /q"]
    DG --> DM3["\\b(mkfs|diskpart)\\b  → disk ops"]
    DG --> DM4["\\bshutdown|reboot|poweroff\\b"]
    DG --> DM5[":\\(\\)\\s\\{.*\\};\\s:  → fork bomb"]
    DG --> DM6["history.jsonl / .dream_cursor\n    redirects"]
    DG --> DM7["tee.*history.jsonl"]
    DG --> DM8["dd.*history.jsonl"]
    DG --> DM9["sed -i.*history.jsonl"]

    AG --> AO["\\bcurl\\b  → allow curl only"]
    AG --> AW["\\bwget\\b  → allow wget only"]
```

### Path Restriction (`restrict_to_workspace`)

When enabled, any absolute path in the command is checked:
- Must be under `cwd` or the configured `workspace_root`
- Or under the media directory (`get_media_dir()`)
- `..` traversal patterns are blocked

---

## Timeout Handling

```mermaid
sequenceDiagram
    participant E as exec()
    participant P as subprocess
    participant A as asyncio

    E->>A: asyncio.wait_for(process.communicate(), timeout)
    Note over A: effective_timeout = min(timeout, 600)
    A-->>P: wait...
    P-->>A: stdout + stderr
    Note over A: success
    A-->>E: (stdout, stderr)
    E->>E: format + truncate at 10 000

    Note over A: After effective_timeout seconds
    A--xP: CancelledError
    P--xE: (never returns)
    E->>P: _kill_process()
    E->>E: return "Error: Command timed out"
```

- **Default timeout:** 60 seconds
- **Maximum timeout:** 600 seconds
- Timeout is capped at `min(timeout, _MAX_TIMEOUT)` even if a larger value is passed.
- On timeout: process is killed via `_kill_process()` (SIGKILL + zombie reaping).

---

## Parameter Summary

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | — | Shell command to execute |
| `working_dir` | `str?` | configured `working_dir` | Working directory |
| `timeout` | `int` | 60 | Timeout in seconds (max 600) |

---

## Constructor Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `timeout` | `int` | 60 | Default timeout for all commands |
| `working_dir` | `str?` | `None` | Default working directory |
| `deny_patterns` | `list[str]` | [default blocklist] | Regex patterns to block |
| `allow_patterns` | `list[str]` | `[]` | If set, only these patterns are allowed |
| `restrict_to_workspace` | `bool` | `False` | Block access outside workspace |
| `sandbox` | `str` | `""` | Sandbox backend (`bwrap`, `proot`, etc.) |
| `path_append` | `str` | `""` | Append to `PATH` |
| `allowed_env_keys` | `list[str]` | `[]` | Env vars to forward |

---

## Output Formatting

```
STDOUT:
<output>

STDERR:
<stderr_text>

Exit code: <code>
```

- `stderr` is only included if it has non-whitespace content
- Output truncated at **10 000 characters** using a **head+tail** split pattern to show beginning and end

---

## Security Summary

| Concern | Protection |
|---------|-----------|
| Destructive commands | Regex deny patterns (`rm -rf`, `del /f`, etc.) |
| History corruption | Blocks redirects to `history.jsonl` / `.dream_cursor` |
| Credential exfil | Env keys not forwarded to subprocess |
| Internal URL SSRF | `contains_internal_url()` check |
| Path traversal | `..` pattern detection + workspace anchoring |
| External path access | Paths checked against `cwd`/`workspace_root`/`media_dir` |
| Fork bombs | `:(){ :|:& };:` pattern blocked |
| Sandbox escape | Resolved paths checked before sandbox wrap |
