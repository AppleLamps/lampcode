from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config import Config
from agent.context import build_thread_messages, estimate_tokens
from agent.models import (
    AgentMessageItem,
    ContextCompactionItem,
    Thread,
    Turn,
    UserMessageItem,
)
from agent.store import ThreadStore
from model.openrouter import OpenRouterClient

COMPACTION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work."""


@dataclass
class CompactionResult:
    removed_items: int = 0
    summary_chars: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0

    @property
    def performed(self) -> bool:
        return self.removed_items > 0


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
) -> CompactionResult:
    messages = build_thread_messages(thread)
    tokens_before = estimate_tokens(messages)
    if not should_compact(messages, config):
        return CompactionResult(estimated_tokens_before=tokens_before)

    preserve_turns = config.compaction.keep_recent_turns
    if len(thread.turns) <= preserve_turns:
        return CompactionResult(estimated_tokens_before=tokens_before)

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

    summary_messages = [
        {"role": "system", "content": COMPACTION_PROMPT},
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
            ContextCompactionItem(summarized_items=item_count),
            AgentMessageItem(text=summary_text),
        ],
    )

    thread.turns = [compact_turn] + recent_turns
    store.rewrite_turns(thread)
    store.save_thread(thread)

    tokens_after = estimate_tokens(build_thread_messages(thread))
    return CompactionResult(
        removed_items=item_count,
        summary_chars=len(summary_text),
        estimated_tokens_before=tokens_before,
        estimated_tokens_after=tokens_after,
    )
