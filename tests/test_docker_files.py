from pathlib import Path

from agent.config import Config
from agent.execution.docker_files import docker_apply_patch, docker_file_tools_enabled, docker_write_file


def _docker_config(tmp_path: Path, *, file_tools: bool = True) -> Config:
    cfg = Config(cwd=tmp_path, model="test", openrouter_api_key="x")
    cfg.execution.backend = "docker"
    cfg.execution.docker.file_tools_in_container = file_tools
    return cfg


def test_docker_file_tools_disabled_by_default(tmp_path: Path) -> None:
    cfg = _docker_config(tmp_path, file_tools=False)
    assert not docker_file_tools_enabled(cfg)


def test_docker_file_tools_enabled(tmp_path: Path) -> None:
    cfg = _docker_config(tmp_path, file_tools=True)
    assert docker_file_tools_enabled(cfg)


def test_docker_write_file(tmp_path: Path) -> None:
    cfg = _docker_config(tmp_path)
    result, meta = docker_write_file(cfg.cwd, "hello.txt", "hi", cfg)
    assert "Successfully" in result
    assert meta["via_mount"] is True
    assert (tmp_path / "hello.txt").read_text() == "hi"


def test_docker_apply_patch(tmp_path: Path) -> None:
    cfg = _docker_config(tmp_path)
    target = tmp_path / "foo.txt"
    target.write_text("line1\nline2\n")
    patch = """*** Begin Patch
*** Update File: foo.txt
@@
-line2
+line2 patched
*** End Patch
"""
    outcome, meta = docker_apply_patch(cfg.cwd, patch, cfg)
    assert outcome.ok
    assert meta["backend"] == "docker"
    assert "patched" in target.read_text()
