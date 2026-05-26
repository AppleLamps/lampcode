# Action log

Every tool call, approval, compaction, error, and assistant reply is appended to a human-readable log for debugging.

## Log locations

| File | Contents |
|------|----------|
| `.agent-cli/action.log` | All actions for the current project (mirrored from events) |
| `~/.agent-cli/logs/<thread_id>.log` | Full history for one thread/session |

JSONL run recordings (machine-readable) remain under `~/.agent-cli/runs/<thread_id>/<turn_id>.jsonl` when `[recording] enabled = true`.

## Example lines

```
2026-05-26T12:00:00+00:00 user: fix the login bug
2026-05-26T12:00:01+00:00 --- turn started ---
2026-05-26T12:00:05+00:00 tool pending: read_file src/auth.py
2026-05-26T12:00:08+00:00 tool done: run_command status=completed output='1 passed'
2026-05-26T12:00:12+00:00 assistant: Updated the handler…
2026-05-26T12:00:12+00:00 --- turn completed tokens_est=42000 ---
```

## Configuration

```toml
[action_log]
enabled = true
dir = "~/.agent-cli/logs"
mirror_to_project = true
```

Set `mirror_to_project = false` to only keep logs under `dir`.
