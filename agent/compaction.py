from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens, load_project_rules
from agent.models import (
    AgentMessageItem,
    ContextCompactionItem,
    Thread,
    Turn,
    UserMessageItem,
)
from agent.store import ThreadStore
from model.openrouter import OpenRouterClient

if TYPE_CHECKING:
    from agent.hooks.runner import HooksRunner

COMPACTION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work."""

MULTI_COMPACT_WARNING = (
    "Heads up: Long threads and multiple compactions can reduce model accuracy. "
    "Start a new thread when possible to keep work small and targeted."
)


@dataclass
class CompactionResult:
    removed_items: int = 0
    summary_chars: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    compaction_count: int = 0
    warning: str | None = None

    @property
    def performed(self) -> bool:
        return self.removed_items > 0


def count_thread_compactions(thread: Thread) -> int:
    highest = 0
    for turn in thread.turns:
        for item in turn.items:
            if item.type == "contextCompaction":
                index = item.compaction_index if item.compaction_index is not None else 1
                highest = max(highest, index)
    return highest


def first_user_task_snippet(thread: Thread) -> str:
    for turn in thread.turns:
        for item in turn.items:
            if isinstance(item, UserMessageItem) and item.text.strip():
                text = item.text.strip()
                return text[:500] + ("..." if len(text) > 500 else "")
    return ""


def build_compaction_base_instructions(
    *,
    project_rules: str,
    original_task: str,
) -> str:
    parts = [COMPACTION_PROMPT]
    if project_rules.strip():
        parts.append("\n\n# Project rules to preserve in the summary\n")
        parts.append(project_rules.strip()[:4000])
    if original_task.strip():
        parts.append("\n\n# Original user task (preserve intent)\n")
        parts.append(original_task.strip())
    return "\n".join(parts)


def should_compact(messages: list[dict[str, Any]], config: Config) -> bool:
    if not config.compaction.enabled:
        return False
    estimate = estimate_tokens(messages)
    threshold = int(config.context_window_tokens * config.compaction.threshold)
    return estimate >= threshold


def compact_thread_if_needed(
    thread: Thread,
    config: Config,
    store: ThreadStore,
    client: OpenRouterClient,
    *,
    hooks_runner: HooksRunner | None = None,
    project_rules: str = "",
    mid_turn: bool = True,
    force: bool = False,
) -> CompactionResult:
    messages = build_thread_messages(thread, project_rules=project_rules)
    tokens_before = estimate_tokens(messages)
    if mid_turn and not force and not config.compaction.auto_mid_turn:
        return CompactionResult(
            estimated_tokens_before=tokens_before,
            compaction_count=count_thread_compactions(thread),
        )
    if not force and not should_compact(messages, config):
        return CompactionResult(
            estimated_tokens_before=tokens_before,
            compaction_count=count_thread_compactions(thread),
        )

    preserve_turns = config.compaction.keep_recent_turns
    if len(thread.turns) <= preserve_turns:
        return CompactionResult(
            estimated_tokens_before=tokens_before,
            compaction_count=count_thread_compactions(thread),
        )

    return _perform_compaction(
        thread,
        config,
        store,
        client,
        hooks_runner=hooks_runner,
        project_rules=project_rules,
        tokens_before=tokens_before,
    )


def force_compact_thread(
    thread: Thread,
    config: Config,
    store: ThreadStore,
    client: OpenRouterClient,
    *,
    hooks_runner: HooksRunner | None = None,
    project_rules: str = "",
) -> CompactionResult:
    return compact_thread_if_needed(
        thread,
        config,
        store,
        client,
        hooks_runner=hooks_runner,
        project_rules=project_rules,
        mid_turn=False,
        force=True,
    )


def _perform_compaction(
    thread: Thread,
    config: Config,
    store: ThreadStore,
    client: OpenRouterClient,
    *,
    hooks_runner: HooksRunner | None,
    project_rules: str,
    tokens_before: int,
) -> CompactionResult:
    prior_compactions = count_thread_compactions(thread)
    new_compaction_index = prior_compactions + 1
    if hooks_runner:
        hooks_runner.run(
            "on_pre_compact",
            {
                "thread_id": thread.id,
                "compaction_count": prior_compactions,
                "estimated_tokens": tokens_before,
            },
            thread_id=thread.id,
        )

    preserve_turns = config.compaction.keep_recent_turns
    older_turns = thread.turns[:-preserve_turns]
    recent_turns = thread.turns[-preserve_turns:]

    transcript_parts: list[str] = []
    item_count = 0
    for turn in older_turns:
        for item in turn.items:
            item_count += 1
            if isinstance(item, UserMessageItem):
                transcript_parts.append(f"User: {item.text}")
            elif isinstance(item, AgentMessageItem):
                transcript_parts.append(f"Agent: {item.text}")
            elif item.type == "commandExecution":
                transcript_parts.append(
                    f"Command ({item.status}): {item.command}\n{item.output or ''}"
                )
            elif item.type == "fileChange":
                transcript_parts.append(
                    f"FileChange ({item.status}): {item.path} — {item.summary or ''}"
                )
            elif item.type == "contextCompaction":
                transcript_parts.append("[Previous compaction checkpoint]")

    if not project_rules.strip():
        project_rules, _ = load_project_rules(Path(thread.cwd))
    original_task = first_user_task_snippet(thread)
    system_prompt = build_compaction_base_instructions(
        project_rules=project_rules,
        original_task=original_task,
    )

    summary_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": "Summarize the following conversation history:\n\n"
            + "\n\n".join(transcript_parts),
        },
    ]

    summary = client.complete(summary_messages)
    max_chars = config.compaction.summary_max_chars
    if len(summary) > max_chars:
        summary = summary[: max_chars - 20] + "\n[... truncated ...]"
    summary_text = f"[Compaction Summary]\n{summary}"

    compact_turn = Turn(
        status="completed",
        items=[
            ContextCompactionItem(
                summarized_items=item_count,
                compaction_index=new_compaction_index,
            ),
            AgentMessageItem(text=summary_text),
        ],
    )

    thread.turns = [compact_turn] + recent_turns
    store.rewrite_turns(thread)
    store.save_thread(thread)

    warning = MULTI_COMPACT_WARNING if prior_compactions >= 1 else None

    if hooks_runner:
        hooks_runner.run(
            "on_post_compact",
            {
                "thread_id": thread.id,
                "compaction_count": new_compaction_index,
                "removed_items": item_count,
                "summary_chars": len(summary_text),
            },
            thread_id=thread.id,
        )

    tokens_after = estimate_tokens(build_thread_messages(thread, project_rules=project_rules))
    return CompactionResult(
        removed_items=item_count,
        summary_chars=len(summary_text),
        estimated_tokens_before=tokens_before,
        estimated_tokens_after=tokens_after,
        compaction_count=new_compaction_index,
        warning=warning,
    )
