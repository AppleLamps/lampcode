from __future__ import annotations

from pathlib import Path

from agent.tui.composer_draft import (
    ComposerDraft,
    clear_composer_draft,
    load_composer_draft,
    save_composer_draft,
)
from agent.tui.mention_popup import MentionBinding, rebuild_bindings_from_text


def test_save_load_composer_draft_with_bindings(tmp_path: Path) -> None:
    draft = ComposerDraft(
        text="fix @src/main.py please",
        bindings=[
            MentionBinding(
                mention="@src/main.py",
                target="src/main.py",
                kind="file",
            )
        ],
    )
    save_composer_draft(tmp_path, "thread-1", draft)
    loaded = load_composer_draft(tmp_path, "thread-1")
    assert loaded.text == draft.text
    assert len(loaded.bindings) == 1
    assert loaded.bindings[0].target == "src/main.py"


def test_load_rebuilds_bindings_when_missing(tmp_path: Path) -> None:
    (tmp_path / "foo.txt").write_text("x", encoding="utf-8")
    save_composer_draft(
        tmp_path,
        "t2",
        ComposerDraft(text="see @foo.txt", bindings=[]),
    )
    loaded = load_composer_draft(tmp_path, "t2")
    assert any(b.target.endswith("foo.txt") for b in loaded.bindings)


def test_clear_composer_draft(tmp_path: Path) -> None:
    save_composer_draft(tmp_path, "t3", ComposerDraft(text="draft", bindings=[]))
    clear_composer_draft(tmp_path, "t3")
    assert load_composer_draft(tmp_path, "t3").text == ""


def test_rebuild_bindings_from_text(tmp_path: Path) -> None:
    (tmp_path / "bar.py").write_text("pass", encoding="utf-8")
    bindings = rebuild_bindings_from_text("edit @bar.py", tmp_path)
    assert bindings
    assert bindings[0].kind == "file"
