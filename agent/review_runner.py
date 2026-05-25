from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agent.config import Config
from agent.context import build_system_prompt, load_project_rules
from agent.events import EventEmitter
from agent.loop import run_turn
from agent.models import AgentMessageItem, Thread, new_id, utc_now_iso
from agent.review import (
    REVIEW_SYSTEM_APPEND,
    ReviewContext,
    ReviewReport,
    build_review_user_prompt,
    collect_review_context,
    parse_review_markdown,
    review_report_to_json,
)
from agent.sandbox.policy import SandboxMode
from agent.settings import load_skills_config
from agent.skills.discovery import discover_skills
from agent.skills.selector import select_skills
from agent.store import ThreadStore


REVIEW_ALLOWED_TOOLS = ["read_file", "search_repo", "run_command"]


@dataclass
class ReviewRunResult:
    turn_id: str
    thread_id: str
    report_markdown: str
    report_json: str | None
    structured: dict[str, Any] | None
    report: ReviewReport | None = None


def run_review(
    config: Config,
    *,
    ctx: ReviewContext,
    store: ThreadStore | None = None,
    events: EventEmitter | None = None,
    auto_approve: bool = False,
    allow_fix: bool = False,
    json_output: bool = False,
    model_profile: str | None = None,
) -> ReviewRunResult:
    store = store or ThreadStore()
    if allow_fix:
        review_config = config
    else:
        review_config = Config.resolve(
            cwd=config.cwd,
            model=config.model,
            auto_approve=auto_approve or config.auto_approve,
            sandbox=SandboxMode.READ_ONLY.value,
            model_profile=model_profile,
            config_path=config.config_path,
        )

    thread = Thread(
        id=new_id(),
        cwd=str(config.cwd),
        model=review_config.model,
        title=ctx.title,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    store.create_thread(thread)

    skills_cfg = load_skills_config(review_config.config_path)
    prompt = build_review_user_prompt(ctx)
    allowed = None if allow_fix else REVIEW_ALLOWED_TOOLS

    turn = run_turn(
        thread,
        prompt,
        review_config,
        store,
        events=events,
        allowed_tools=allowed,
        system_prompt_append=REVIEW_SYSTEM_APPEND,
        read_only_review=not allow_fix,
        thread_title=ctx.title,
        headless_json=json_output,
        session_auto_approve=auto_approve,
    )

    final_text = ""
    for item in reversed(turn.items):
        if isinstance(item, AgentMessageItem):
            final_text = item.text
            break

    report = parse_review_markdown(final_text)
    cost = turn.usage.estimated_cost_usd
    structured = {
        "summary": report.summary,
        "findings": [
            {
                "severity": f.severity,
                "title": f.title,
                "detail": f.detail,
                "file": f.file,
                "line": f.line,
            }
            for f in report.findings
        ],
        "severity_counts": report.severity_counts,
        "suggested_fixes": report.suggested_fixes,
        "test_gaps": report.test_gaps,
        "thread_id": thread.id,
        "model": turn.usage.model_used or review_config.model,
        "cost": cost,
    }
    if ctx.merge_base_sha is not None:
        structured["merge_base_sha"] = ctx.merge_base_sha
    if ctx.review_scope:
        structured["review_scope"] = ctx.review_scope
    if ctx.custom_prompt:
        structured["custom_prompt"] = ctx.custom_prompt
    report_json = review_report_to_json(
        report,
        thread_id=thread.id,
        model=structured["model"],
        cost=cost,
        merge_base_sha=ctx.merge_base_sha,
        review_scope=ctx.review_scope,
        custom_prompt=ctx.custom_prompt,
    )
    return ReviewRunResult(
        turn_id=turn.id,
        thread_id=thread.id,
        report_markdown=report.raw_markdown or final_text,
        report_json=report_json,
        structured=structured,
        report=report,
    )
