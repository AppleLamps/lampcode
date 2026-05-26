"""TUI layout: bottom chrome must not be overlapped by main content."""

from __future__ import annotations

from agent.tui.theme import GROK_CSS


def test_bottom_chrome_not_docked_overlapping_main() -> None:
    """Bottom chrome lives in app_shell flow so main cannot paint over it."""
    assert "#app_shell" in GROK_CSS
    assert "#bottom_chrome" in GROK_CSS
    assert "dock: bottom" not in GROK_CSS.split("#bottom_chrome")[1].split("#")[0]


def test_main_has_min_height_zero_for_flex_shrink() -> None:
    assert "min-height: 0" in GROK_CSS
