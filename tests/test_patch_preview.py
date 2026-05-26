"""Tests for patch approval preview helpers."""

from pathlib import Path

from agent.tui.patch_preview import (
    patch_approval_diff_preview,
    patch_files_from_arguments,
)


def test_patch_files_from_arguments_add() -> None:
    patch = """*** Begin Patch
*** Add File: foo.txt
+hello
*** End Patch"""
    files = patch_files_from_arguments({"patch": patch})
    assert len(files) == 1
    assert files[0].path == "foo.txt"
    assert files[0].change_type == "add"


def test_patch_approval_diff_preview(tmp_path: Path) -> None:
    patch = """*** Begin Patch
*** Add File: bar.txt
+line
*** End Patch"""
    preview = patch_approval_diff_preview({"patch": patch}, cwd=tmp_path)
    assert preview is not None
    assert "bar.txt" in preview or "+line" in preview
