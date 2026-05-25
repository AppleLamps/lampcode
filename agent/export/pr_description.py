from __future__ import annotations

import subprocess
from pathlib import Path

from agent.models import Thread


def _git_diff_stat(cwd: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "").strip()
    return proc.stdout.strip()


def _summarize_turns(thread: Thread, *, max_turns: int = 8) -> list[str]:
    lines: list[str] = []
    start = max(1, len(thread.turns) - max_turns + 1)
    for i, turn in enumerate(thread.turns[-max_turns:], start=start):
        user = next((it.text for it in turn.items if it.type == "userMessage"), "")
        assistant = next((it.text for it in turn.items if it.type == "agentMessage"), "")
        files: list[str] = []
        commands: list[str] = []
        for it in turn.items:
            if it.type == "fileChange" and it.path:
                files.append(it.path)
            elif it.type == "commandExecution" and it.command:
                commands.append(it.command)
        block = [f"### Turn {i} ({turn.status})"]
        if user:
            block.append(f"**User:** {user[:500]}")
        if assistant:
            block.append(f"**Assistant:** {assistant[:800]}")
        if files:
            block.append("**Files:** " + ", ".join(f"`{p}`" for p in files[:10]))
        if commands:
            block.append("**Commands:** " + "; ".join(f"`{c}`" for c in commands[:5]))
        if turn.usage and turn.usage.estimated_cost_usd:
            block.append(
                f"**Usage:** in={turn.usage.input_tokens} out={turn.usage.output_tokens} "
                f"cost≈${turn.usage.estimated_cost_usd:.4f}"
            )
        lines.extend(block)
        lines.append("")
    return lines


def build_pr_description(
    thread: Thread,
    *,
    title: str | None = None,
    include_diff: bool = True,
) -> str:
    """Build a markdown PR description from thread transcript + optional git diff stat."""
    cwd = Path(thread.cwd)
    heading = title or thread.title or thread.display_label()
    parts: list[str] = [
        f"## {heading}",
        "",
        f"- **Thread:** `{thread.id}`",
        f"- **CWD:** `{thread.cwd}`",
        f"- **Model:** {thread.model}",
    ]
    if thread.forked_from:
        parts.append(f"- **Forked from:** `{thread.forked_from}`")
    parts.extend(["", "## Summary", ""])
    last_assistant = ""
    for turn in reversed(thread.turns):
        for it in reversed(turn.items):
            if it.type == "agentMessage" and it.text.strip():
                last_assistant = it.text.strip()
                break
        if last_assistant:
            break
    if last_assistant:
        parts.append(last_assistant[:2000])
    else:
        parts.append("_No assistant summary in thread._")
    parts.extend(["", "## Conversation", ""])
    parts.extend(_summarize_turns(thread))
    if include_diff and cwd.is_dir():
        diff = _git_diff_stat(cwd)
        parts.extend(["## Git diff stat", ""])
        if diff:
            parts.append("```")
            parts.append(diff[:4000])
            parts.append("```")
        else:
            parts.append("_No git diff or not a git repository._")
    return "\n".join(parts).rstrip() + "\n"
