from __future__ import annotations

from agent.tui.diff_render import (
    infer_path_from_diff,
    format_diff_lines,
    _split_hunks,
)


def test_infer_path_from_diff() -> None:
    diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n+x"
    assert infer_path_from_diff(diff) == "foo.py"


def test_split_hunks() -> None:
    lines = ["+++ b/x.py", "@@ -1 +1 @@", "+a", "@@ -2 +2 @@", "+b"]
    hunks = _split_hunks(lines)
    assert len(hunks) == 2
    assert hunks[0][0].startswith("+++")


def test_format_diff_lines_hunk_separator() -> None:
    diff = (
        "--- a/a.py\n+++ b/a.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
        "@@ -4,2 +4,2 @@\n"
        "-two\n"
        "+too\n"
    )
    rendered = format_diff_lines(
        diff,
        max_lines=50,
        source_path="a.py",
        syntax_highlight=False,
        hunk_aware=True,
    )
    assert "" in rendered
