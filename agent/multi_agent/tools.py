from __future__ import annotations

SPAWN_WORKER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_worker",
        "description": (
            "Delegate a subtask to a worker agent (forked thread). "
            "Worker runs asynchronously; use wait_workers to collect results. "
            "Optional depends_on waits for listed worker_ids to complete first."
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
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Worker IDs that must complete before this worker starts.",
                },
            },
            "required": ["task"],
        },
    },
}

SPAWN_WORKER_BATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "spawn_worker_batch",
        "description": (
            "Register multiple workers as a DAG slice atomically. "
            "Each task may include depends_on worker_id references."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "worker_id": {"type": "string"},
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "title": {"type": "string"},
                            "model": {"type": "string"},
                            "execution_backend": {
                                "type": "string",
                                "enum": ["local", "docker", "ssh"],
                            },
                        },
                        "required": ["task"],
                    },
                },
            },
            "required": ["tasks"],
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
                "mode": {
                    "type": "string",
                    "enum": ["all", "any", "deps"],
                    "description": "all=wait every target; any=return when first completes; deps=wait dependency closure.",
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

GET_WORKER_GRAPH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_worker_graph",
        "description": "Return the worker DAG nodes, edges, and statuses for the current turn.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

MULTI_AGENT_TOOL_SCHEMAS = [
    SPAWN_WORKER_SCHEMA,
    SPAWN_WORKER_BATCH_SCHEMA,
    WAIT_WORKERS_SCHEMA,
    LIST_WORKERS_SCHEMA,
    GET_WORKER_GRAPH_SCHEMA,
]
