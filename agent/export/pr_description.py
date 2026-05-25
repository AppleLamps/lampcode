from __future__ import annotations

import re
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


def summarize_thread_for_pr(thread: Thread) -> list[str]:
    """Bullet summary of changes, tests, and outcomes for a PR body."""
    bullets: list[str] = []
    files_changed: dict[str, str] = {}
    test_cmds: list[str] = []
    last_assistant = ""

    for turn in thread.turns:
        for it in turn.items:
            if it.type == "agentMessage" and it.text.strip():
                last_assistant = it.text.strip()
            elif it.type == "fileChange" and it.path and it.path != "(patch)":
                files_changed[it.path] = it.summary or it.change_type or "changed"
            elif it.type == "commandExecution" and it.command:
                cmd = it.command.strip()
                if re.search(r"\bpytest\b", cmd, re.I):
                    status = it.status or "ran"
                    test_cmds.append(f"`{cmd}` ({status})")

    if last_assistant:
        first_line = last_assistant.splitlines()[0][:160]
        bullets.append(f"**Outcome:** {first_line}")

    if files_changed:
        shown = list(files_changed.items())[:8]
        file_bits = [f"`{p}` ({kind})" for p, kind in shown]
        extra = len(files_changed) - len(shown)
        if extra > 0:
            file_bits.append(f"+{extra} more")
        bullets.append("**Files:** " + ", ".join(file_bits))

    if test_cmds:
        bullets.append("**Tests:** " + "; ".join(test_cmds[:4]))
    elif any("test" in (t.status or "") for t in thread.turns):
        bullets.append("**Tests:** see thread commands")

    total_cost = sum(
        (t.usage.estimated_cost_usd or 0.0) for t in thread.turns if t.usage
    )
    if total_cost > 0:
        bullets.append(f"**Agent cost:** ≈${total_cost:.4f}")

    if not bullets:
        bullets.append("_No structured summary — see conversation below._")
    return bullets


def _summarize_turns(thread: Thread, *, max_turns: int = 8) -> list[str]:
    lines: list[str] = []
    start = max(1, len(thread.turns) - max_turns + 1)
    for i, turn in enumerate(thread.turns[-max_turns:], start=start):
        user = next((it.text for it in turn.items if it.type == "userMessage"), "")
        assistant = next((it.text for it in turn.items if it.type == "agentMessage"), "")
        files: list[str] = []
        commands: list[str] = []
        for it in turn.items:
            if it.type == "fileChange" and it.path and it.path != "(patch)":
                files.append(it.path)
            elif it.type == "commandExecution" and it.command:
                commands.append(it.command)
        block = [f"### Turn {i} ({turn.status})"]
        if user:
            block.append(f"**User:** {user[:500]}")
        if assistant and "cancelled by user" not in assistant.lower():
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
    include_conversation: bool = True,
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
    for bullet in summarize_thread_for_pr(thread):
        parts.append(f"- {bullet}")
    if include_conversation and thread.turns:
        parts.extend(["", "## Conversation", ""])
        parts.extend(_summarize_turns(thread))
    if include_diff and cwd.is_dir():
        diff = _git_diff_stat(cwd)
        parts.extend(["", "## Git diff stat", ""])
        if diff:
            parts.append("```")
            parts.append(diff[:4000])
            parts.append("```")
        else:
            parts.append("_No git diff or not a git repository._")
    return "\n".join(parts).rstrip() + "\n"
