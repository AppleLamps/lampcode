"""Pre-turn context compaction guard."""
from __future__ import annotations

from agent.compaction import compact_thread_if_needed, compact_tool_outputs
from agent.config import Config
from agent.context import build_thread_messages
from agent.context_meter import build_context_snapshot, invalidate_context_cache
from agent.models import Thread
from agent.store import ThreadStore

def run_pre_turn_context_guard(
    thread: Thread,
    config: Config,
    store: ThreadStore,
    client,
    *,
    project_rules: str,
    active_skills,
    skills_max_body: int,
    memories_text: str,
    system_prompt_append: str,
    hooks_runner,
) -> list | None:
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    pre_limit = int(snap.effective_window * config.compaction.pre_turn_threshold)
    if snap.estimated_total <= pre_limit:
        return None

    t1 = compact_tool_outputs(thread, config, store, project_rules=project_rules)
    snap = build_context_snapshot(config, thread, ctx_settings=config.context)
    if snap.estimated_total <= pre_limit:
        return rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )

    cr = compact_thread_if_needed(
        thread,
        config,
        store,
        client,
        hooks_runner=hooks_runner,
        project_rules=project_rules,
        force=True,
    )
    if cr.performed:
        invalidate_context_cache(thread.id)
        return rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )
    if t1.performed:
        return rebuild_messages(
            thread,
            config,
            project_rules=project_rules,
            active_skills=active_skills,
            skills_max_body=skills_max_body,
            memories_text=memories_text,
            system_prompt_append=system_prompt_append,
        )
    return None


def rebuild_messages(
    thread: Thread,
    config: Config,
    *,
    project_rules: str,
    active_skills,
    skills_max_body: int,
    memories_text: str,
    system_prompt_append: str,
) -> list:
    return build_thread_messages(
        thread,
        active_skills=active_skills,
        skills_max_body=skills_max_body,
        project_rules=project_rules,
        execution_backend=config.execution.backend,
        sync_enabled=config.execution.ssh.sync_enabled,
        memories_text=memories_text,
        system_prompt_append=system_prompt_append,
    )


def spill_item_output(
    config: Config,
    *,
    thread_id: str,
    item_id: str,
    text: str | None,
) -> tuple[str | None, str | None, int | None]:
    if not text:
        return text, None, None
    from agent.artifacts import spill_if_large

    limit = min(config.max_tool_output, config.context.artifact_inline_limit)
    inline, path = spill_if_large(
        text, thread_id=thread_id, item_id=item_id, inline_limit=limit
    )
    return inline, path, len(text)
