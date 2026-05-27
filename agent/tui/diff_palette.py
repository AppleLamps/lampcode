"""Codex-style diff color palettes for truecolor, 256-color, and 16-color terminals."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ColorLevel(str, Enum):
    TRUECOLOR = "truecolor"
    INDEXED_256 = "256"
    ANSI_16 = "16"


class DiffTheme(str, Enum):
    DARK = "dark"
    LIGHT = "light"


class DiffLineKind(str, Enum):
    INSERT = "insert"
    DELETE = "delete"
    CONTEXT = "context"
    META = "meta"


# Truecolor RGB tuples (from Codex diff_render.rs)
_DARK_TC_ADD = (33, 58, 43)  # #213A2B
_DARK_TC_DEL = (74, 34, 29)  # #4A221D
_LIGHT_TC_ADD = (218, 251, 225)  # #dafbe1
_LIGHT_TC_DEL = (255, 235, 233)  # #ffebe9
_LIGHT_TC_ADD_NUM = (172, 238, 187)  # #aceebb
_LIGHT_TC_DEL_NUM = (255, 206, 203)  # #ffcecb
_LIGHT_TC_GUTTER_FG = (31, 35, 40)  # #1f2328

# 256-color indices (Codex)
_DARK_256_ADD = 22
_DARK_256_DEL = 52
_LIGHT_256_ADD = 194
_LIGHT_256_DEL = 224
_LIGHT_256_ADD_NUM = 157
_LIGHT_256_DEL_NUM = 217
_LIGHT_256_GUTTER_FG = 236


@dataclass(frozen=True)
class DiffPalette:
    theme: DiffTheme
    level: ColorLevel

    @classmethod
    def detect(cls) -> DiffPalette:
        return cls(theme=detect_diff_theme(), level=detect_color_level())


def detect_color_level() -> ColorLevel:
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return ColorLevel.TRUECOLOR
    term = os.environ.get("TERM", "").lower()
    if "256color" in term or "xterm-256" in term:
        return ColorLevel.INDEXED_256
    try:
        from rich.console import Console

        console = Console()
        if console.color_system == "truecolor":
            return ColorLevel.TRUECOLOR
        if console.color_system == "256":
            return ColorLevel.INDEXED_256
    except Exception:
        pass
    return ColorLevel.ANSI_16


def detect_diff_theme() -> DiffTheme:
    """Infer light vs dark terminal background (COLORFGBG); default dark for agent-cli."""
    raw = os.environ.get("COLORFGBG", "").strip()
    if raw:
        parts = raw.split(";")
        if parts:
            try:
                bg = int(parts[-1])
                if bg in (7, 15):
                    return DiffTheme.LIGHT
            except ValueError:
                pass
    return DiffTheme.DARK


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _on_256(idx: int) -> str:
    return f"color({idx})"


def _line_bg(palette: DiffPalette, kind: DiffLineKind) -> str | None:
    if palette.level == ColorLevel.ANSI_16:
        return None
    if kind == DiffLineKind.INSERT:
        if palette.level == ColorLevel.TRUECOLOR:
            rgb = _LIGHT_TC_ADD if palette.theme == DiffTheme.LIGHT else _DARK_TC_ADD
            return _rgb_hex(rgb)
        idx = _LIGHT_256_ADD if palette.theme == DiffTheme.LIGHT else _DARK_256_ADD
        return _on_256(idx)
    if kind == DiffLineKind.DELETE:
        if palette.level == ColorLevel.TRUECOLOR:
            rgb = _LIGHT_TC_DEL if palette.theme == DiffTheme.LIGHT else _DARK_TC_DEL
            return _rgb_hex(rgb)
        idx = _LIGHT_256_DEL if palette.theme == DiffTheme.LIGHT else _DARK_256_DEL
        return _on_256(idx)
    return None


def _gutter_bg(palette: DiffPalette, kind: DiffLineKind) -> str | None:
    if palette.level != ColorLevel.TRUECOLOR or palette.theme != DiffTheme.LIGHT:
        return None
    if kind == DiffLineKind.INSERT:
        return _rgb_hex(_LIGHT_TC_ADD_NUM)
    if kind == DiffLineKind.DELETE:
        return _rgb_hex(_LIGHT_TC_DEL_NUM)
    return None


def _gutter_fg(palette: DiffPalette, kind: DiffLineKind) -> str | None:
    if palette.theme == DiffTheme.LIGHT and kind in (DiffLineKind.INSERT, DiffLineKind.DELETE):
        if palette.level == ColorLevel.TRUECOLOR:
            return _rgb_hex(_LIGHT_TC_GUTTER_FG)
        if palette.level == ColorLevel.INDEXED_256:
            return _on_256(_LIGHT_256_GUTTER_FG)
    return None


def _wrap(bg: str | None, fg: str | None, text: str) -> str:
    if bg and fg:
        return f"[{fg} on {bg}]{text}[/{fg} on {bg}]"
    if bg:
        return f"[on {bg}]{text}[/on {bg}]"
    if fg:
        return f"[{fg}]{text}[/{fg}]"
    return text


def format_line_number(palette: DiffPalette, num: int | None) -> str:
    if num is None:
        return ""
    return f"{num:>4} "


def format_gutter_sign(
    palette: DiffPalette,
    kind: DiffLineKind,
    sign: str,
) -> str:
    if kind == DiffLineKind.INSERT:
        fg = "green"
    elif kind == DiffLineKind.DELETE:
        fg = "red"
    elif kind == DiffLineKind.META:
        return f"[cyan]{sign}[/cyan]"
    else:
        fg = "dim"
    bg = _gutter_bg(palette, kind) if kind in (DiffLineKind.INSERT, DiffLineKind.DELETE) else None
    gutter_fg = _gutter_fg(palette, kind)
    if gutter_fg:
        fg = gutter_fg
    return _wrap(bg, fg, sign)


def format_diff_body(
    palette: DiffPalette,
    kind: DiffLineKind,
    inner: str,
) -> str:
    """Format diff line body (after gutter sign) with theme-aware background."""
    if kind == DiffLineKind.META:
        return f"[cyan]{inner}[/cyan]"
    if kind == DiffLineKind.CONTEXT:
        if inner.strip():
            return inner
        return f"[dim]{inner}[/dim]"

    if palette.level == ColorLevel.ANSI_16:
        color = "green" if kind == DiffLineKind.INSERT else "red"
        return f"[{color}]{inner}[/{color}]"

    bg = _line_bg(palette, kind)
    if kind == DiffLineKind.INSERT:
        return _wrap(bg, None, inner)
    if kind == DiffLineKind.DELETE:
        return _wrap(bg, None, inner)
    return inner


def format_diff_row(
    palette: DiffPalette,
    *,
    line_number: int | None,
    kind: DiffLineKind,
    sign: str,
    body: str,
) -> str:
    """Full Rich-markup row: line number + gutter + body."""
    num = format_line_number(palette, line_number)
    gutter = format_gutter_sign(palette, kind, sign)
    content = format_diff_body(palette, kind, body)
    return f"{num}{gutter}{content}"
