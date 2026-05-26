"""Codex-style assistant streaming with code-fence and table holdback."""

from __future__ import annotations

from agent.tui.table_holdback import table_holdback_suffix


class AssistantStreamController:
    """Buffers assistant deltas; avoids flashing incomplete fenced code blocks."""

    def __init__(self) -> None:
        self._committed_len: int = 0
        self._holdback: str = ""

    def reset(self) -> None:
        self._committed_len = 0
        self._holdback = ""

    def absorb(self, full_text: str) -> str:
        """Return new suffix safe to display (may hold back mid-fence or table)."""
        if len(full_text) <= self._committed_len:
            return ""
        table_tail = table_holdback_suffix(full_text)
        display_text = full_text
        if table_tail:
            hold_from = len(full_text) - len(table_tail)
            if hold_from > self._committed_len:
                display_text = full_text[:hold_from]
            elif hold_from <= self._committed_len:
                return ""
        incoming = self._holdback + display_text[self._committed_len :]
        self._holdback = ""
        if not incoming:
            return ""

        display_parts: list[str] = []
        while incoming:
            fence = incoming.find("```")
            if fence < 0:
                display_parts.append(incoming)
                incoming = ""
                break
            if fence > 0:
                display_parts.append(incoming[:fence])
                incoming = incoming[fence:]
            close = incoming.find("```", 3)
            if close < 0:
                self._holdback = incoming
                incoming = ""
                break
            end = close + 3
            display_parts.append(incoming[:end])
            incoming = incoming[end:]

        suffix = "".join(display_parts)
        self._committed_len = len(full_text)
        return suffix

    def flush_remainder(self, full_text: str) -> str:
        """Emit any held-back tail when the assistant block is finalized."""
        if len(full_text) > self._committed_len:
            tail = full_text[self._committed_len :]
            self._holdback = ""
            self._committed_len = len(full_text)
            return tail
        if self._holdback:
            held = self._holdback
            self._holdback = ""
            return held
        return ""
