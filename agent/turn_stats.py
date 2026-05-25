from __future__ import annotations

import re
from dataclasses import dataclass

from agent.models import CommandExecutionItem, FileChangeItem, Turn


@dataclass
class TurnStats:
    files_touched: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    commands_run: int = 0
    tests_detected: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "files_touched": self.files_touched,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "commands_run": self.commands_run,
            "tests_detected": self.tests_detected,
        }


def _count_diff_lines(snippet: str | None) -> tuple[int, int]:
    if not snippet:
        return 0, 0
    added = removed = 0
    for line in snippet.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _output_mentions_tests(text: str | None) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(marker in lower for marker in ("pytest", "tests passed", "test session", " unittest"))


def aggregate_turn_stats(turn: Turn) -> TurnStats:
    stats = TurnStats()
    seen_files: set[str] = set()
    for item in turn.items:
        if isinstance(item, FileChangeItem) and item.status == "completed":
            if item.path not in seen_files:
                seen_files.add(item.path)
                stats.files_touched += 1
            added, removed = _count_diff_lines(item.diff_snippet)
            stats.lines_added += added
            stats.lines_removed += removed
        elif isinstance(item, CommandExecutionItem) and item.status == "completed":
            stats.commands_run += 1
            if _output_mentions_tests(item.output) or _output_mentions_tests(item.command):
                stats.tests_detected = True
    return stats


def format_turn_stats_brief(stats: TurnStats) -> str:
    parts = [
        f"files={stats.files_touched}",
        f"+{stats.lines_added}/-{stats.lines_removed}",
        f"cmds={stats.commands_run}",
    ]
    if stats.tests_detected:
        parts.append("tests=yes")
    return " ".join(parts)
