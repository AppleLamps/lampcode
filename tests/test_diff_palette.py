from __future__ import annotations

import os

import pytest

from agent.tui.diff_palette import (
    ColorLevel,
    DiffLineKind,
    DiffPalette,
    DiffTheme,
    detect_color_level,
    detect_diff_theme,
    format_diff_body,
    format_diff_row,
)
from agent.tui.diff_render import format_diff_line, reset_diff_palette_cache


@pytest.fixture(autouse=True)
def _reset_palette_caches() -> None:
    reset_diff_palette_cache()


def test_detect_color_level_truecolor() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("TERM", raising=False)
    assert detect_color_level() == ColorLevel.TRUECOLOR
    monkeypatch.undo()


def test_detect_diff_theme_light_from_colorfgbg() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("COLORFGBG", "0;15")
    assert detect_diff_theme() == DiffTheme.LIGHT
    monkeypatch.undo()


def test_dark_truecolor_add_row() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    row = format_diff_row(
        pal,
        line_number=1,
        kind=DiffLineKind.INSERT,
        sign="+",
        body="hello",
    )
    assert "#213a2b" in row.lower()
    assert "+" in row


def test_dark_truecolor_delete_row() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    row = format_diff_row(
        pal,
        line_number=None,
        kind=DiffLineKind.DELETE,
        sign="-",
        body="bye",
    )
    assert "#4a221d" in row.lower()


def test_ansi_16_uses_foreground_only() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.ANSI_16)
    body = format_diff_body(pal, DiffLineKind.INSERT, "x")
    assert "[green]" in body
    assert "on " not in body


def test_format_diff_line_respects_forced_palette() -> None:
    pal = DiffPalette(theme=DiffTheme.DARK, level=ColorLevel.TRUECOLOR)
    line = format_diff_line("+x", syntax_highlight=False, palette=pal)
    assert "#213a2b" in line.lower()
