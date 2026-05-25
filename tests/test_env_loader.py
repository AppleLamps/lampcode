from __future__ import annotations

import os
from pathlib import Path

from agent.env_loader import load_env_file, load_dotenv_files


def test_load_env_file_sets_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text('OPENROUTER_API_KEY="from-dotenv"\n', encoding="utf-8")
    count = load_env_file(env_path)
    assert count == 1
    assert os.environ["OPENROUTER_API_KEY"] == "from-dotenv"


def test_load_env_file_skips_existing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "already-set")
    env_path = tmp_path / ".env"
    env_path.write_text("OPENROUTER_API_KEY=from-dotenv\n", encoding="utf-8")
    load_env_file(env_path)
    assert os.environ["OPENROUTER_API_KEY"] == "already-set"


def test_load_dotenv_files_from_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=cwd-key\n", encoding="utf-8")
    loaded = load_dotenv_files(cwd=tmp_path)
    assert any(p.name == ".env" for p in loaded)
    assert os.environ["OPENROUTER_API_KEY"] == "cwd-key"
