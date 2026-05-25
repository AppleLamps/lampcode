from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from agent.settings import CrossThreadGitSyncSettings


GitRunner = Callable[..., subprocess.CompletedProcess[str] | Any]


class GitProgramBackend:
    name = "git"

    def __init__(
        self,
        settings: CrossThreadGitSyncSettings,
        *,
        cwd: Path | None = None,
        git_run: GitRunner | None = None,
    ) -> None:
        self.settings = settings
        self.cwd = Path(cwd or Path.cwd())
        self.repo_root = (self.cwd / settings.repo_path).expanduser().resolve()
        self._git_run = git_run or self._default_git_run

    def _default_git_run(self, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(args),
            cwd=str(self.repo_root),
            capture_output=True,
            text=True,
            timeout=kwargs.get("timeout", 60),
        )

    def _ensure_repo(self) -> None:
        self.repo_root.mkdir(parents=True, exist_ok=True)
        if not (self.repo_root / ".git").is_dir():
            self._git_run("git", "init", "-b", self.settings.branch)
        else:
            self._git_run("git", "checkout", self.settings.branch)

    def _program_path(self, program_id: str) -> Path:
        return self.repo_root / f"{program_id}.json"

    def push(self, program_id: str, payload: bytes) -> None:
        self._ensure_repo()
        path = self._program_path(program_id)
        path.write_bytes(payload)
        self._git_run("git", "add", str(path.name))
        msg = self.settings.auto_commit_message
        self._git_run("git", "commit", "-m", msg, "--allow-empty")
        self._git_run("git", "push", self.settings.remote, self.settings.branch)

    def pull(self, program_id: str) -> bytes | None:
        if not self.repo_root.is_dir():
            return None
        self._git_run("git", "pull", self.settings.remote, self.settings.branch)
        path = self._program_path(program_id)
        if path.is_file():
            return path.read_bytes()
        return None

    def list_program_ids(self) -> list[str]:
        if not self.repo_root.is_dir():
            return []
        return sorted(p.stem for p in self.repo_root.glob("*.json"))
