from pathlib import Path

import pytest

from agent.paths import is_path_within_cwd, resolve_path_within_cwd


def test_resolve_relative_path(tmp_path: Path) -> None:
    sub = tmp_path / "src"
    sub.mkdir()
    resolved = resolve_path_within_cwd(tmp_path, "src/main.py")
    assert resolved == (sub / "main.py").resolve()


def test_reject_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="escapes"):
        resolve_path_within_cwd(tmp_path, "../outside.txt")


def test_reject_absolute_outside(tmp_path: Path) -> None:
    outside = tmp_path.parent / "other"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_path_within_cwd(tmp_path, str(outside / "file.txt"))


def test_is_path_within_cwd(tmp_path: Path) -> None:
    inner = tmp_path / "a" / "b.txt"
    inner.parent.mkdir()
    inner.write_text("hi")
    assert is_path_within_cwd(tmp_path, inner)
    assert not is_path_within_cwd(tmp_path, tmp_path.parent)
