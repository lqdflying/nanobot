# `agent/memory.py` — MemoryStore, Consolidator, Dream

Pure file I/O store, lightweight token-budget consolidation, and heavyweight cron-scheduled memory processing.

---

## MemoryStore (`agent/memory.py`)

Pure file I/O for memory files: `MEMORY.md`, `history.jsonl`, `SOUL.md`, `USER.md`.

| Method | Description |
|--------|-------------|
| `read_memory()` / `write_memory(content)` | Long-term facts stored in `memory/MEMORY.md` |
| `read_soul()` / `write_soul(content)` | Agent identity stored in `SOUL.md` |
| `read_user()` / `write_user(content)` | User profile stored in `USER.md` |
| `get_memory_context()` | Returns `"## Long-term Memory\n<longterm>"` for prompt injection |
| `append_history(entry)` → `cursor: int` | Append to `history.jsonl` (append-only, JSONL) |
| `read_unprocessed_history(since_cursor)` → list | Entries with `cursor > since_cursor` |
| `compact_history()` | Drop oldest entries if file exceeds `max_history_entries` |
| `raw_archive(messages)` | Fallback: dump raw messages to `history.jsonl` without LLM |
| `read_file(path)` → `str` | Static helper for reading any file |
| `get_last_dream_cursor()` / `set_last_dream_cursor(cursor)` | Dream cursor tracking |

JSONL format:
```json
{"cursor": 1, "timestamp": "2026-04-18 10:27", "content": "..."}
```

Legacy migration: `HISTORY.md` → `history.jsonl` (one-time, on first read if `history.jsonl` is empty).

---

## Consolidator

Lightweight consolidation: token-budget triggered, summarizes evicted messages into `history.jsonl` via LLM.

### `maybe_consolidate_by_tokens(session)`

Loop: archive old messages until prompt fits within safe budget.

```python
async def maybe_consolidate_by_tokens(self, session: Session) -> None:
```

**Budget calculation:**
```
budget = context_window_tokens - max_completion_tokens - _SAFETY_BUFFER
target = budget // 2
```

**Algorithm:**
1. Estimate current prompt tokens via `estimate_session_prompt_tokens(session)`
2. If `estimated <= budget` → idle (log `unconsolidated_count`)
3. If `estimated > budget` → loop up to `_MAX_CONSOLIDATION_ROUNDS` (5):
   - `pick_consolidation_boundary()` finds a user-turn boundary that removes enough tokens
   - `_cap_consolidation_boundary()` clamps chunk size to `_MAX_CHUNK_MESSAGES` (60)
   - `archive(chunk)` → LLM summarization → `store.append_history(summary)`
   - Update `session.last_consolidated` and save session
   - Re-estimate and repeat

**Lock:** per-session `asyncio.Lock` (stored in weak dict) to prevent concurrent consolidation.

### `archive(messages)`

Summarize messages via LLM and append to `history.jsonl`.

```python
async def archive(self, messages: list[dict]) -> str | None:
```

- Formats messages via `MemoryStore._format_messages()`
- Calls LLM with `agent/consolidator_archive.md` system prompt
- On success: `store.append_history(summary)`, return summary
- On failure: call `store.raw_archive(messages)` (no data loss)

---

## `raw_archive()` — Fallback (No Data Loss)

When LLM summarization fails, raw-dump the messages to `history.jsonl`:

```python
def raw_archive(self, messages: list[dict]) -> None:
    self.append_history(
        f"[RAW] {len(messages)} messages\n"
        f"{self._format_messages(messages)}"
    )
    logger.warning("Memory consolidation degraded: raw-archived {} messages", len(messages))
```

Key property: **no data loss** — even if every LLM call fails, messages are preserved in history.

---

## Dream — Experimental Memory Processor

Two-phase heavyweight cron-scheduled memory consolidation.

### Phase 1: LLM Analysis

Plain LLM call (no tools) on:
- Unprocessed `history.jsonl` entries (batch of `max_batch_size=20`)
- Current `MEMORY.md` (with optional per-line age annotation: `← Nd` suffix for lines >14 days stale)
- Current `SOUL.md` and `USER.md`

Output: analysis summary (what changed, what to remember, what skills are relevant).

### Phase 2: AgentRunner Edit

`AgentRunner.run()` with `read_file` / `edit_file` / `write_file` tools:
- Targeted, incremental edits to `MEMORY.md`, `SOUL.md`, `USER.md`
- Creates new skills if analysis suggests them (via `skill-creator` template)

### `_annotate_with_ages(content)`

Appends `← Nd` suffix to lines in `MEMORY.md` older than `_STALE_THRESHOLD_DAYS` (default 14 days).

```python
def _annotate_with_ages(self, content: str) -> str:
    # Uses GitStore.line_ages() for per-line modification ages
    for line, age in zip(lines, ages):
        if age.age_days > _STALE_THRESHOLD_DAYS:
            annotated.append(f"{line}  ← {age.age_days}d")
```

Skips annotation if git unavailable or line-count mismatch (working-tree vs HEAD).

---

## `_legacy_fallback_timestamp()`

When migrating legacy `HISTORY.md` entries, assigns a fallback timestamp from file mtime:

```python
def _legacy_fallback_timestamp(self) -> str:
    try:
        return datetime.fromtimestamp(
            self.legacy_history_file.stat().st_mtime,
        ).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d %H:%M")
```

---

## Consolidation Behaviour

| Scenario | Behaviour |
|----------|-----------|
| Tokens within budget | No consolidation, log idle with unconsolidated count |
| Tokens exceed budget | Loop: pick boundary → archive → re-estimate → repeat |
| LLM succeeds | Summarized entry appended to `history.jsonl` |
| LLM fails | `raw_archive()` — raw messages dumped, no data loss |
| Chunk too large | Clamped to `_MAX_CHUNK_MESSAGES` (60) at user-turn boundary |
| No safe boundary found | Abort round, log debug, return |
| Session lock held | Other sessions proceed concurrently (weak dict per-key) |

---

## Key Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_MAX_CONSOLIDATION_ROUNDS` | 5 | Max archive loops per call |
| `_MAX_CHUNK_MESSAGES` | 60 | Hard cap per consolidation round |
| `_SAFETY_BUFFER` | 1024 | Extra headroom for tokenizer estimation drift |
| `_STALE_THRESHOLD_DAYS` | 14 | Dream line annotation threshold |
| `max_batch_size` (Dream) | 20 | Max history entries per Dream run |
| `max_iterations` (Dream) | 10 | Max tool-call iterations in Dream Phase 2 |