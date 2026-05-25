# Hook events (subprocess JSON I/O)

Hooks are configured in `~/.agent-cli/hooks.json` or `{project}/.agent-cli/hooks.json`.
Each hook receives JSON on stdin and may return JSON on stdout.

## Supported events

| Event | When | stdin payload | stdout JSON |
|-------|------|---------------|-------------|
| `on_session_start` | Turn begins | `{thread_id, turn_id, cwd, model}` | informational |
| `on_user_prompt_submit` | Before model loop | `{thread_id, turn_id, prompt}` | `{context_append?}` |
| `on_permission_request` | Before approval prompt | `{tool_name, arguments, summary}` | `{context_append?}` |
| `on_pre_tool_use` | Before tool dispatch | `{tool_name, arguments}` | `{decision: "block", reason?}` |
| `on_tool_pending` | Tool queued | `{tool_name, arguments}` | informational |
| `on_turn_completed` | Turn ends | `{thread_id, turn_id, status}` | informational |
| `on_pre_compact` | Before compaction | `{thread_id, compaction_count, estimated_tokens}` | informational |
| `on_post_compact` | After compaction | `{thread_id, compaction_count, removed_items}` | informational |

Environment: `AGENT_HOOK_EVENT`, `AGENT_THREAD_ID`, `AGENT_TURN_ID`, optional `AGENT_TOOL_NAME`.

Non-zero exit → hook failed. With `[hooks] fail_on_error = true`, the turn aborts.
