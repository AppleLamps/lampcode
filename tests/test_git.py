import os
import subprocess
from pathlib import Path

from agent.git import detect_repo_root, is_inside_git_repo


def test_detect_repo_root(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "file.txt").write_text("x")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t.com",
        },
    )
    root = detect_repo_root(tmp_path)
    assert root == str(tmp_path.resolve())
    assert is_inside_git_repo(tmp_path)


def test_non_git_dir_returns_none(tmp_path: Path) -> None:
    assert detect_repo_root(tmp_path) is None
    assert not is_inside_git_repo(tmp_path)
