# CronTool — Scheduling Reminders & Recurring Tasks

**File:** `tools/cron.py`
**Tool name:** `cron`

Schedules background jobs that fire based on intervals, cron expressions, or one-time timestamps. Backed by `CronService`. Results can be delivered to the user's channel automatically.

---

## Architecture

```mermaid
flowchart LR
    subgraph AgentLoop
        CT["CronTool"]
    end

    subgraph CronService
        CJ1["CronJob"]
        CJ2["CronJob"]
        CJ3["CronJob"]
        Queue["Background queue"]
    end

    CT -->|"add_job / remove_job / list_jobs"| CronService
    CronService --> CJ1
    CronService --> CJ2
    CronService --> CJ3
    CJ1 --> Queue
    Queue -->|"deliver=true"| Channel["User Channel"]
```

---

## Per-Action Schema

```mermaid
flowchart TD
    A["cron(...)"]

    A -->|"action=\"add\""| ADD["_add_job()"]
    ADD --> AM1["✓ message required"]
    ADD --> AM2["every_seconds | cron_expr | at\n required (one of)"]
    ADD --> AM3["tz → only with cron_expr"]
    ADD --> AM4["deliver: bool (default true)"]

    A -->|"action=\"list\""| LIST["_list_jobs()"]
    LIST --> LM1["No parameters needed"]

    A -->|"action=\"remove\""| REMOVE["_remove_job()"]
    REMOVE --> RM1["✓ job_id required"]
    REMOVE --> RM2["protected jobs cannot be removed"]

    ADD -->|"missing message"| E1["Error: message required"]
    ADD -->|"no timing param"| E2["Error: timing required"]
    REMOVE -->|"missing job_id"| E3["Error: job_id required"]
```

### Parameter Requirements by Action

| Parameter | `add` | `list` | `remove` |
|-----------|:-----:|:------:|:--------:|
| `action` | ✅ Required | ✅ Required | ✅ Required |
| `message` | ✅ **Required** | Not used | Not used |
| `job_id` | Not used | Not used | ✅ **Required** |
| `name` | Optional | Not used | Not used |
| `every_seconds` | Optional | Not used | Not used |
| `cron_expr` | Optional | Not used | Not used |
| `tz` | Optional (with cron_expr) | Not used | Not used |
| `at` | Optional | Not used | Not used |
| `deliver` | Optional | Not used | Not used |

---

## Scheduling Modes

```mermaid
flowchart TD
    S["schedule kind?"]
    S -->|"every"| E["CronSchedule(kind='every',\nevery_ms = seconds × 1000)"]
    S -->|"cron"| C["CronSchedule(kind='cron',\nexpr, tz)"]
    S -->|"at"| T["CronSchedule(kind='at',\nat_ms = timestamp)"]
    T -->|"at is one-time"| DA["delete_after_run = True"]
```

### Timing String Formatting

| Kind | Example Output |
|------|---------------|
| `every` (hours) | `"every 24h"` |
| `every` (minutes) | `"every 60m"` |
| `every` (seconds) | `"every 30s"` |
| `every` (ms) | `"every 500ms"` |
| `cron` | `"cron: 0 9 * * * (Asia/Singapore)"` |
| `at` | `"at 2026-06-01T09:00:00+08:00 (Asia/Singapore)"` |

---

## CronService Integration

```mermaid
sequenceDiagram
    participant Agent
    participant CronTool
    participant CronService
    participant Scheduler as Background Scheduler)
    participant Channel

    Agent->>CronTool: cron(action="add", message="...", every_seconds=3600)
    CronTool->>CronTool: _add_job()
    CronTool->>CronService: add_job(name, schedule, message, deliver, channel, to)
    CronService-->>CronTool: CronJob(id)
    CronTool-->>Agent: "Created job '...' (id: abc123)"

    Note over Scheduler: Fires every hour
    Scheduler->>CronService: callback fires
    CronService->>CronTool: deliver result
    CronTool->>Channel: deliver=true → send to user
```

---

## `deliver` Flag

```mermaid
flowchart LR
    F["cron(action=\"add\", deliver=?)"]
    F -->|deliver=true| D1["Result sent to user channel"]
    F -->|deliver=false| D2["Silent background execution\n no user notification"]
```

- **`deliver=true` (default):** When the job fires, the result is delivered to the session's channel/chat_id.
- **`deliver=false`:** The job runs silently. Useful for background maintenance tasks that don't need user-facing output.

The session `channel` and `chat_id` are captured via `set_context()` when the tool is called during an active session and restored in cron callbacks for delivery.

---

## Context Guard

```mermaid
flowchart LR
    R["cron callback running"]
    R -->|"action=\"add\""| G["_in_cron_context.get()"]
    G -->|True| E["Error: cannot schedule\nfrom within cron job"]
    G -->|False| OK["job added normally"]
```

A `ContextVar` (`_in_cron_context`) tracks whether execution is happening inside a cron callback. Jobs cannot create child jobs to prevent infinite recursion.

---

## Error Handling

| Situation | Response |
|----------|---------|
| `action="add"` without `message` | `"Error: cron action='add' requires a non-empty 'message'..."` |
| `action="add"` with no timing param | `"Error: either every_seconds, cron_expr, or at is required"` |
| `tz` without `cron_expr` | `"Error: tz can only be used with cron_expr"` |
| Invalid timezone | `"Error: unknown timezone 'Foo'"` |
| Invalid ISO datetime for `at` | `"Error: invalid ISO datetime format '...'` |
| `action="remove"` without `job_id` | `"Error: job_id is required for remove"` |
| Remove protected system job | `"Cannot remove job 'dream'. This is a system-managed..."` |
| Remove non-existent job | `"Job abc123 not found"` |
| Cron callback tries to add job | `"Error: cannot schedule new jobs from within a cron job execution"` |

---

## CronJob Structure

```mermaid
classDiagram
    direction LR

    class CronJob {
        +id: str
        +name: str
        +schedule: CronSchedule
        +payload: CronPayload
        +state: CronJobState
        +channel: str
        +to: str
        +delete_after_run: bool
        +created_at_ms: int
    }

    class CronSchedule {
        +kind: "every" | "cron" | "at"
        +every_ms: int?
        +expr: str?
        +tz: str?
        +at_ms: int?
    }

    class CronJobState {
        +last_run_at_ms: int?
        +last_status: str?
        +last_error: str?
        +next_run_at_ms: int?
    }

    CronJob --> CronSchedule
    CronJob --> CronJobState
```

---

## System Jobs

| Job Name | Purpose | Removable |
|----------|---------|:---------:|
| `dream` | Dream memory consolidation for long-term memory | ❌ No (protected) |

System jobs show `Purpose: Dream memory consolidation...` in list output and return `"Protected: visible for inspection, but cannot be removed."` on remove attempt.

---

## Usage Examples

```python
# Recurring every hour
cron(action="add", message="Check disk space and report if > 80%", every_seconds=3600)
# → Created job 'Check disk space...' (id: abc123)

# Daily cron at 9 AM SGT
cron(action="add", message="Daily standup reminder",
     cron_expr="0 9 * * *", tz="Asia/Singapore")
# → Created job 'Daily standup reminder' (id: def456)

# One-time reminder
cron(action="add", message="Team meeting in 5 minutes",
     at="2026-04-20T10:25:00")
# → Created job 'Team meeting in 5...' (id: ghi789)

# Silent background task
cron(action="add", message="Sync logs to backup", every_seconds=86400, deliver=false)
# → Created job 'Sync logs to backup' (id: jkl012)

# List all jobs
cron(action="list")
# → Scheduled jobs:
#   - Daily standup reminder (id: def456, cron: 0 9 * * * (Asia/Singapore))
#     Next run: 2026-04-21T09:00:00+08:00 (Asia/Singapore)
#   - Check disk space... (id: abc123, every 1h)
#     Next run: 2026-04-20T11:00:00 (UTC)

# Remove a job
cron(action="remove", job_id="abc123")
# → Removed job abc123
```
