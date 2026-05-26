from __future__ import annotations

from agent.tui.transcript_pane import TranscriptPane


def test_is_near_bottom_when_at_top_is_false() -> None:
    """Default scroll position is top; must not treat that as 'following'."""

    class FakeScroll:
        scroll_y = 0
        max_scroll_y = 100

    pane = TranscriptPane.__new__(TranscriptPane)
    pane._scroll = FakeScroll()  # type: ignore[attr-defined]
    assert pane.is_near_bottom() is False


def test_is_near_bottom_when_at_end_is_true() -> None:
    class FakeScroll:
        scroll_y = 98
        max_scroll_y = 100

    pane = TranscriptPane.__new__(TranscriptPane)
    pane._scroll = FakeScroll()  # type: ignore[attr-defined]
    assert pane.is_near_bottom() is True
