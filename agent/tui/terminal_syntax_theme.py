"""Map terminal palette to Rich/Pygments syntax themes for diffs and markdown."""

from __future__ import annotations

from agent.tui.diff_palette import ColorLevel, DiffPalette, DiffTheme, detect_color_level, detect_diff_theme

# Rich Syntax / Markdown code_theme names (Pygments styles).
_DARK_RICH = "github-dark"
_LIGHT_RICH = "github-light"
_DARK_16 = "ansi_dark"
_LIGHT_16 = "ansi_light"

_cached: tuple[str, str] | None = None


def reset_syntax_theme_cache() -> None:
    global _cached
    _cached = None


def syntax_theme_for_palette(palette: DiffPalette) -> str:
    """Pygments style name for Rich Syntax and Markdown fences."""
    if palette.level == ColorLevel.ANSI_16:
        return _LIGHT_16 if palette.theme == DiffTheme.LIGHT else _DARK_16
    if palette.theme == DiffTheme.LIGHT:
        return _LIGHT_RICH
    return _DARK_RICH


def resolve_syntax_themes(
    palette: DiffPalette | None = None,
) -> tuple[str, str]:
    """Return (code_fence_theme, inline_code_theme) for Rich Markdown."""
    global _cached
    if palette is None and _cached is not None:
        return _cached
    pal = palette or DiffPalette.detect()
    theme = syntax_theme_for_palette(pal)
    pair = (theme, theme)
    if palette is None:
        _cached = pair
    return pair


def syntax_highlight_enabled(palette: DiffPalette) -> bool:
    """Skip heavy highlighting when the terminal only supports 16 colors."""
    return palette.level != ColorLevel.ANSI_16


def detect_palette() -> DiffPalette:
    return DiffPalette(theme=detect_diff_theme(), level=detect_color_level())
