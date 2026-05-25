# JSON event stream (`agent run --json`)

Normalized JSONL events emitted on stdout during `agent run --json`. Each line is one JSON object with:

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Event name (see catalog below) |
| `ts` | string | ISO-8601 timestamp |
| `thread_id` | string \| null | Thread identifier |
| `turn_id` | string \| null | Turn identifier |
| `payload` | object | Event-specific fields |

Legacy `--jsonl-events` uses the internal `AgentEvent` shape instead.

## Event catalog

| Event | When | Key payload fields |
|-------|------|-------------------|
| `thread.started` | Run begins | `title` |
| `turn.started` | User turn begins | — |
| `turn.completed` | Turn ends | `status`, `estimated_tokens` |
| `agent.delta` | Assistant text streams | `text` |
| `tool.pending` | Tool call queued | `tool_name`, `arguments`, `source` |
| `tool.completed` | Tool finished | `tool_name`, `status`, `source`, `summary` |
| `command.execution` | Shell item recorded | `item_type`, `status` |
| `file.change` | Patch/file item recorded | `item_type`, `status` |
| `approval.requested` | User approval needed | `summary`, `tool_name` |
| `approval.decided` | User responded | `decision` |
| `compaction.completed` | Context compacted | `removed_items`, `estimated_tokens_before`, `estimated_tokens_after` |
| `compaction.warning` | Multi-compact warning | `message` |
| `plan.proposed` | Plan mode structured plan | `text`, `summary` |
| `permission.escalated` | Sandbox scope granted | `scope` |
| `permission.denied` | Escalation denied | `scope`, `reason` |
| `error` | Error surfaced | `message` |
| `run.summary` | End of run (synthetic) | `status`, `model`, `cost`, `stats`, `budget_exceeded` |
| `run.result` | Structured final output | review or schema-validated JSON |

## Example sequence

```json
{"type":"thread.started","ts":"2026-05-25T12:00:00+00:00","thread_id":"abc","turn_id":null,"payload":{"title":"fix tests"}}
{"type":"turn.started","ts":"2026-05-25T12:00:01+00:00","thread_id":"abc","turn_id":"turn-1","payload":{}}
{"type":"tool.pending","ts":"...","thread_id":"abc","turn_id":"turn-1","payload":{"tool_name":"read_file","arguments":{"path":"calc.py"}}}
{"type":"tool.completed","ts":"...","thread_id":"abc","turn_id":"turn-1","payload":{"tool_name":"read_file","status":"completed"}}
{"type":"turn.completed","ts":"...","thread_id":"abc","turn_id":"turn-1","payload":{"status":"completed"}}
{"type":"run.summary","ts":"...","thread_id":"abc","turn_id":"turn-1","payload":{"status":"completed","model":"anthropic/claude-sonnet-4","cost":0.012,"stats":{"files_touched":1,"commands_run":1}}}
```

See `tests/fixtures/json_stream_golden.jsonl` for a committed golden fixture used in CI.
