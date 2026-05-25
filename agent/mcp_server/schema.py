from __future__ import annotations

AGENT_RUN_TOOL_NAME = "agent_run"

AGENT_RUN_INPUT_SCHEMA: dict = {
    "type": "object",
    "required": ["prompt"],
    "properties": {
        "prompt": {"type": "string", "description": "User prompt for the agent turn"},
        "cwd": {"type": "string", "description": "Working directory for the run"},
        "model": {"type": "string", "description": "OpenRouter model slug override"},
        "sandbox_mode": {
            "type": "string",
            "enum": ["read-only", "workspace-write", "danger-full-access"],
            "description": "Sandbox mode override",
        },
        "auto_approve": {
            "type": "boolean",
            "description": "Auto-approve tool prompts (required for headless CI)",
        },
        "max_cost": {
            "type": "number",
            "description": "Per-turn max cost in USD",
        },
        "profile": {
            "type": "string",
            "description": "Model profile name from config",
        },
    },
    "additionalProperties": False,
}
