from __future__ import annotations

from agent.tui.cells.error import render_approval_banner_text
from agent.tui.diff_render import strip_rich_markup


def test_approval_banner_includes_patch_warning() -> None:
    text = strip_rich_markup(
        render_approval_banner_text(
            "apply_patch: foo.py",
            tool_name="apply_patch",
        )
    )
    assert "No diff preview" in text


def test_approval_banner_with_diff() -> None:
    text = strip_rich_markup(
        render_approval_banner_text(
            "apply_patch: foo.py",
            diff_preview="+++ b/foo.py\n@@ -0,0 +1 @@\n+line",
            tool_name="apply_patch",
            source_path="foo.py",
        )
    )
    assert "Patch preview" in text
    assert "+line" in text
