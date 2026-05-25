from __future__ import annotations

SPAWN_WORKER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_worker",
        "description": (
            "Delegate a subtask to a worker agent (forked thread). "
            "Worker runs asynchronously; use wait_workers to collect results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Subtask prompt for the worker."},
                "title": {"type": "string", "description": "Optional worker thread title."},
                "model": {"type": "string", "description": "Optional model override."},
                "worker_id": {
                    "type": "string",
                    "description": "Optional label/id for this worker (auto-generated if omitted).",
                },
                "execution_backend": {
                    "type": "string",
                    "enum": ["local", "docker", "ssh"],
                    "description": "Optional execution backend for worker shell commands.",
                },
            },
            "required": ["task"],
        },
    },
}

WAIT_WORKERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "wait_workers",
        "description": "Wait for worker agents to complete and return structured summaries.",
        "parameters": {
            "type": "object",
            "properties": {
                "worker_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Worker IDs to wait for (null/omit = all workers in this turn).",
                },
                "timeout_sec": {
                    "type": "integer",
                    "description": "Optional timeout override in seconds.",
                },
            },
            "required": [],
        },
    },
}

LIST_WORKERS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_workers",
        "description": "List workers spawned in this turn with status.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

MULTI_AGENT_TOOL_SCHEMAS = [
    SPAWN_WORKER_SCHEMA,
    WAIT_WORKERS_SCHEMA,
    LIST_WORKERS_SCHEMA,
]
