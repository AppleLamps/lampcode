from __future__ import annotations

import pytest

from agent.tui.diff_palette import ColorLevel, DiffPalette, DiffTheme
from agent.tui.terminal_syntax_theme import (
    reset_syntax_theme_cache,
    resolve_syntax_themes,
    syntax_highlight_enabled,
    syntax_theme_for_palette,
)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_syntax_theme_cache()
    yield
    reset_syntax_theme_cache()


def test_syntax_theme_dark_truecolor() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    assert syntax_theme_for_palette(pal) == "github-dark"


def test_syntax_theme_light_truecolor() -> None:
    pal = DiffPalette(theme=DiffTheme.LIGHT, level=ColorLevel.TRUECOLOR)
    assert syntax_theme_for_palette(pal) == "github-light"


def test_syntax_theme_ansi_16_dark() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.ANSI_16)
    assert syntax_theme_for_palette(pal) == "ansi_dark"
    assert syntax_highlight_enabled(pal) is False


def test_resolve_syntax_themes_cached() -> None:
    a = resolve_syntax_themes(
        DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    )
    b = resolve_syntax_themes(
        DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    )
    assert a == b == ("github-dark", "github-dark")
