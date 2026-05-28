from __future__ import annotations


def line_col_to_lsp(line_1based: int, character_0based: int, text: str) -> tuple[int, int]:
    """Convert 1-based line and 0-based UTF-8 column to LSP 0-based line + UTF-16 character."""
    lines = text.splitlines()
    line_0 = max(0, line_1based - 1)
    if line_0 >= len(lines):
        return line_0, 0
    line_text = lines[line_0]
    col = max(0, min(character_0based, len(line_text)))
    utf16 = len(line_text[:col].encode("utf-16-le")) // 2
    return line_0, utf16
