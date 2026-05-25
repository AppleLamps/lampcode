from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_MODEL = "anthropic/claude-sonnet-4"
DEFAULT_MAX_ROUNDS = 25
DEFAULT_COMMAND_TIMEOUT = 120
DEFAULT_MAX_TOOL_OUTPUT = 20_000


@dataclass
class Config:
    cwd: Path
    model: str
    auto_approve: bool = False
    max_rounds: int = DEFAULT_MAX_ROUNDS
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT
    max_tool_output: int = DEFAULT_MAX_TOOL_OUTPUT
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    @classmethod
    def from_env(
        cls,
        cwd: str | Path,
        *,
        model: str | None = None,
        auto_approve: bool = False,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        command_timeout: int = DEFAULT_COMMAND_TIMEOUT,
    ) -> Config:
        resolved_cwd = Path(cwd).resolve()
        if not resolved_cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {resolved_cwd}")

        api_key = os.environ.get("OPENROUTER_API_KEY")
        base_url = os.environ.get(
            "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
        resolved_model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)

        return cls(
            cwd=resolved_cwd,
            model=resolved_model,
            auto_approve=auto_approve,
            max_rounds=max_rounds,
            command_timeout=command_timeout,
            openrouter_api_key=api_key,
            openrouter_base_url=base_url.rstrip("/"),
        )

    def require_api_key(self) -> str:
        if not self.openrouter_api_key:
            raise ValueError(
                "OPENROUTER_API_KEY is required. Set it in your environment:\n"
                "  export OPENROUTER_API_KEY=your_key_here"
            )
        return self.openrouter_api_key
