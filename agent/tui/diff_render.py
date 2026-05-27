"""Shared diff formatting for TUI and CLI output."""

from __future__ import annotations

import re
from pathlib import Path

from agent.tui.diff_palette import (
    DiffLineKind,
    DiffPalette,
    format_diff_row,
)
from agent.tui.terminal_syntax_theme import (
    syntax_highlight_enabled,
    syntax_theme_for_palette,
)

_CHANGE_PREFIX = {"add": "A", "update": "M", "delete": "D", "overwrite": "M"}

_EXT_LEXER: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".xml": "xml",
}

_PALETTE: DiffPalette | None = None


def _palette() -> DiffPalette:
    global _PALETTE
    if _PALETTE is None:
        _PALETTE = DiffPalette.detect()
    return _PALETTE


def reset_diff_palette_cache() -> None:
    """For tests."""
    global _PALETTE
    _PALETTE = None
    from agent.tui.terminal_syntax_theme import reset_syntax_theme_cache

    reset_syntax_theme_cache()


def format_file_summary(files: list[tuple[str, str]]) -> str:
    """Format file list as 'A foo.py  M bar.py  D baz.py'."""
    parts: list[str] = []
    for path, change_type in files:
        prefix = _CHANGE_PREFIX.get(change_type, "M")
        parts.append(f"{prefix} {path}")
    return "  ".join(parts)


def lexer_for_path(path: str | None) -> str | None:
    if not path:
        return None
    return _EXT_LEXER.get(Path(path).suffix.lower())


def infer_path_from_diff(diff_text: str | None) -> str | None:
    if not diff_text:
        return None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            return line[6:].strip()
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path != "/dev/null":
                return path
    return None


def _split_hunks(lines: list[str]) -> list[list[str]]:
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("@@"):
            if any(h.startswith("@@") for h in current):
                hunks.append(current)
                current = [line]
            else:
                current.append(line)
        else:
            current.append(line)
    if current:
        hunks.append(current)
    return hunks if hunks else [lines]


def _line_kind(line: str) -> DiffLineKind:
    if line.startswith("@@") or line.startswith("+++") or line.startswith("---"):
        return DiffLineKind.META
    if line.startswith("+") and not line.startswith("+++"):
        return DiffLineKind.INSERT
    if line.startswith("-") and not line.startswith("---"):
        return DiffLineKind.DELETE
    return DiffLineKind.CONTEXT


def _highlight_hunk_content_lines(
    hunk_lines: list[str],
    lexer: str,
    *,
    palette: DiffPalette,
) -> dict[int, str]:
    indexed: list[tuple[int, str, str]] = []
    for idx, line in enumerate(hunk_lines):
        if line.startswith(("+", "-", " ")) and not line.startswith(("+++", "---")):
            body = line[1:] if len(line) > 1 else ""
            indexed.append((idx, line[0], body))

    if not indexed:
        return {}

    block = "\n".join(body for _, _, body in indexed)
    highlighted = _highlight_code(block, lexer, palette=palette)
    if not highlighted:
        return {}

    split = highlighted.split("\n")
    if len(split) != len(indexed):
        return {}

    return {idx: markup for (idx, _, _), markup in zip(indexed, split, strict=False)}


def _highlight_code(
    body: str,
    lexer: str | None,
    *,
    palette: DiffPalette | None = None,
) -> str | None:
    if not lexer or not body.strip():
        return None
    pal = palette or _palette()
    if not syntax_highlight_enabled(pal):
        return None
    try:
        from rich.syntax import Syntax
    except ImportError:
        return None
    try:
        text = Syntax(
            body,
            lexer,
            theme=syntax_theme_for_palette(pal),
            line_numbers=False,
            word_wrap=False,
            background_color="default",
        ).highlight()
        return text.markup
    except Exception:
        return None


def _render_physical_line(
    palette: DiffPalette,
    line: str,
    *,
    line_number: int | None,
    lexer: str | None,
    syntax_highlight: bool,
    hunk_markup: dict[int, str] | None,
    local_idx: int,
) -> str:
    kind = _line_kind(line)
    if kind == DiffLineKind.META:
        return format_diff_row(
            palette,
            line_number=line_number,
            kind=kind,
            sign="",
            body=line,
        )

    sign = line[0] if line else " "
    body = line[1:] if len(line) > 1 else ""

    if hunk_markup and local_idx in hunk_markup and kind in (
        DiffLineKind.INSERT,
        DiffLineKind.DELETE,
        DiffLineKind.CONTEXT,
    ):
        body = hunk_markup[local_idx]
    elif syntax_highlight and lexer and body.strip():
        inner = _highlight_code(body, lexer, palette=palette)
        if inner:
            body = inner

    return format_diff_row(
        palette,
        line_number=line_number,
        kind=kind,
        sign=sign,
        body=body,
    )


def format_diff_line(
    line: str,
    *,
    lexer: str | None = None,
    syntax_highlight: bool = True,
    palette: DiffPalette | None = None,
) -> str:
    """Format a single diff physical line with Codex-style palette."""
    return _render_physical_line(
        palette or _palette(),
        line,
        line_number=None,
        lexer=lexer,
        syntax_highlight=syntax_highlight,
        hunk_markup=None,
        local_idx=0,
    )


def format_diff_lines(
    diff_text: str,
    *,
    max_lines: int = 40,
    line_numbers: bool = False,
    source_path: str | None = None,
    syntax_highlight: bool = True,
    hunk_aware: bool = True,
    palette: DiffPalette | None = None,
) -> list[str]:
    """Return Rich-markup lines for a unified diff snippet."""
    pal = palette or _palette()
    path = source_path or infer_path_from_diff(diff_text)
    lexer = lexer_for_path(path)
    lines = str(diff_text).splitlines()
    total = len(lines)
    if total > max_lines:
        lines = lines[:max_lines] + [f"... ({total - max_lines} more lines)"]

    result: list[str] = []
    line_no = 1
    hunks = _split_hunks(lines) if hunk_aware else [lines]

    for hunk_idx, hunk in enumerate(hunks):
        if hunk_idx > 0:
            result.append("")
        hunk_markup = None
        if hunk_aware and syntax_highlight and lexer:
            hunk_markup = _highlight_hunk_content_lines(hunk, lexer, palette=pal)
        for local_idx, line in enumerate(hunk):
            result.append(
                _render_physical_line(
                    pal,
                    line,
                    line_number=line_no if line_numbers else None,
                    lexer=lexer,
                    syntax_highlight=syntax_highlight,
                    hunk_markup=hunk_markup,
                    local_idx=local_idx,
                )
            )
            line_no += 1
    return result


def strip_rich_markup(text: str) -> str:
    """Remove Rich markup tags for golden test comparison."""
    return re.sub(r"\[[^\]]+\]", "", text)
