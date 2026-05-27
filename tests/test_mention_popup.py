from __future__ import annotations

from pathlib import Path

from agent.tui.mention_popup import (
    active_mention_suffix,
    apply_mention,
    list_mention_candidates,
)


def test_active_mention_suffix() -> None:
    assert active_mention_suffix("fix @src/au") == "src/au"
    assert active_mention_suffix("hello world") is None


def test_apply_mention_replaces_trailing_partial(tmp_path: Path) -> None:
    (tmp_path / "foo.py").write_text("x", encoding="utf-8")
    from agent.tui.mention_popup import MentionCandidate

    cand = MentionCandidate(label="foo.py", insert="@foo.py ", kind="file")
    out = apply_mention("please edit @fo", cand)
    assert out == "please edit @foo.py "


def test_list_mention_candidates_includes_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass", encoding="utf-8")
    cands = list_mention_candidates("look at @ma", tmp_path)
    assert any("main.py" in c.label for c in cands)
