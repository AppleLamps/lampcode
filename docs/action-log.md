# Action log

Every tool call, approval, compaction, error, and assistant reply is appended to a human-readable log for debugging.

## Log locations

| File | Contents |
|------|----------|
| `.agent-cli/action.log` | All actions for the current project (mirrored from events) |
| `~/.agent-cli/logs/<thread_id>.log` | Full history for one thread/session |

JSONL run recordings (machine-readable) remain under `~/.agent-cli/runs/<thread_id>/<turn_id>.jsonl` when `[recording] enabled = true`.

The project mirror (`.agent-cli/action.log`) reflects **whichever thread is active in the TUI** for that cwd. If you resume a different session, check the per-thread file under `~/.agent-cli/logs/` for the exact `thread_id` (also in the log header).

## Dev servers (default)

With `[execution] auto_background_servers = true` (default), commands like `python -m http.server` or `npm run dev` are **detached automatically**: the tool returns in under a second with a pid, and the turn continues. Set `auto_background_servers = false` in `.agent-cli/config.toml` to restore old foreground+timeout behavior. Use `run_command` with `"background": false` to force waiting for one command.

## Tool lifecycle lines

LSP MCP tools appear as `mcp__lsp__lsp_definition` (etc.) with the same pending → executing → done sequence when `[mcp_servers.lsp]` is connected.

For each tool the harness emits up to three lines:

| Line | Meaning |
|------|---------|
| `tool pending: …` | Model requested the tool; UI queued it |
| `tool executing: …` | Harness passed hooks/approval and started `dispatch_tool` |
| `tool done: …` | Result returned (`status=completed`, `failed`, `denied`, …) |

Example:

```
2026-05-26T20:23:36+00:00 tool pending: run_command dir /b *.html
2026-05-26T20:23:36+00:00 item started: commandExecution id=4795f919
2026-05-26T20:23:36+00:00 tool done: run_command status=completed output='index.html\n'
2026-05-26T20:23:38+00:00 tool pending: read_file index.html
2026-05-26T20:23:38+00:00 tool executing: read_file index.html
2026-05-26T20:23:38+00:00 tool done: read_file status=completed output='     1|<!DOCTYPE html>…'
```

`read_file` / `search_repo` do not emit `item started` (no persisted execution item), but they still log `pending` → `executing` → `done`.

## Other example lines

```
2026-05-26T12:00:00+00:00 user: fix the login bug
2026-05-26T12:00:01+00:00 --- turn started ---
2026-05-26T12:00:05+00:00 reasoning: Let me inspect the auth module…
2026-05-26T12:00:08+00:00 approval requested: apply_patch: src/auth.py
2026-05-26T12:00:12+00:00 assistant: Updated the handler…
2026-05-26T12:00:12+00:00 --- turn completed tokens_est=42000 ---
2026-05-26T12:00:12+00:00 ERROR: context length exceeded
```

Assistant text is buffered during streaming and written on `--- turn completed ---` (not on every token).

## Debugging a stuck turn

1. Open `.agent-cli/action.log` or `~/.agent-cli/logs/<thread_id>.log` and read the **last line**.
2. Interpret:

| Last line | Likely cause |
|-----------|----------------|
| `tool pending` only | Stuck before dispatch (hooks, approval, or UI thread backlog). With current TUI, events are non-blocking; reinstall if you still see long gaps here. |
| `tool executing` only | Stuck inside `dispatch_tool` (shell, MCP, large file I/O). |
| `tool done` then silence | Waiting on the model API (`stream_completion`) or compaction pre-turn guard. |
| `--- turn started ---` only | First model request slow or hung (check network / OpenRouter status). |

3. In the TUI, **Ctrl+C** cancels the turn. If the spinner keeps running after the worker exits, the UI watchdog should clear it within ~1s; quit and restart if needed.
4. Compare with machine-readable events: `~/.agent-cli/runs/<thread_id>/<turn_id>.jsonl`.

## Configuration

```toml
[action_log]
enabled = true
dir = "~/.agent-cli/logs"
mirror_to_project = true
```

Set `mirror_to_project = false` to only keep logs under `dir`.

## TUI event delivery

The interactive UI drains agent events on the main Textual thread via `post_message` (see `agent/tui/messages.py`). The turn worker must **not** call `call_from_thread` per event — that blocks the worker on `future.result()` while the UI renders transcript and context chrome, which can freeze tool execution and leave stale “Working…” state.

## Related

- [ui-plan.md](ui-plan.md) — transcript architecture (v2.11.0)
- [code-navigation.md](code-navigation.md) — LSP MCP tool names in logs
- [docs/README.md](README.md) — documentation index
