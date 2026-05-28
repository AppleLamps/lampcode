"""Special builtin tools: user input and permission escalation."""
from __future__ import annotations

import json
from typing import Any

from agent.config import Config
from agent.events import EventEmitter
from agent.models import Thread, Turn, UserInputItem
from agent.session import HarnessSession
from agent.store import ThreadStore
from approval.gate import (
    TurnApprovalState,
    format_tool_summary,
    needs_approval_prompt,
    prompt_approval,
)

def handle_request_user_input(
    thread: Thread,
    turn: Turn,
    store: ThreadStore,
    emitter: EventEmitter,
    arguments: dict,
    config: Config,
    *,
    headless_json: bool,
    session: HarnessSession,
    turn_state: TurnApprovalState,
) -> str:
    from agent.user_input import normalize_user_input_questions, resolve_user_input

    specs = normalize_user_input_questions(arguments)
    if not specs:
        return json.dumps({"error": "question or questions required"})

    if not config.auto_approve and needs_approval_prompt(
        "request_user_input", arguments, config, turn_state=turn_state, session=session
    ):
        summary = format_tool_summary("request_user_input", arguments)
        emitter.approval_requested(thread.id, turn.id, "request_user_input", summary)
        if not prompt_approval(
            "request_user_input",
            arguments,
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        ):
            return json.dumps({"error": "user denied input prompt"})

    total = len(specs)
    results: list[dict[str, Any]] = []

    for index, spec in enumerate(specs, start=1):
        question = spec["question"]
        options = spec.get("options")
        allow_free = bool(spec.get("allow_free_text", True))

        emitter.user_input_requested(
            thread.id,
            turn.id,
            question=question,
            options=options,
            allow_free_text=allow_free,
            question_index=index,
            question_total=total,
        )
        answer, selected, err = resolve_user_input(
            question,
            options,
            allow_free_text=allow_free,
            auto_approve=config.auto_approve,
            headless_json=headless_json,
            question_key=question,
            question_index=index,
            question_total=total,
        )
        if err:
            emitter.error(thread.id, err)
            return json.dumps({"error": err, "partial_answers": results})

        item = UserInputItem(
            question=question,
            answer=answer or "",
            selected_option=selected,
            options=options or [],
        )
        turn.items.append(item)
        store.append_item(thread, turn.id, item)
        emitter.user_input(
            thread.id,
            turn.id,
            question=question,
            answer=answer or "",
            selected_option=selected,
        )
        entry = {"question": question, "answer": answer, "selected_option": selected}
        results.append(entry)

    first = results[0]
    payload: dict[str, Any] = {
        "answer": first.get("answer"),
        "selected_option": first.get("selected_option"),
        "answers": results,
    }
    return json.dumps(payload)


def handle_request_permissions(
    thread: Thread,
    turn: Turn,
    emitter: EventEmitter,
    arguments: dict,
    config: Config,
    *,
    session: HarnessSession,
    turn_state: TurnApprovalState,
    read_only_review: bool,
) -> str:
    if read_only_review:
        emitter.permission_denied(
            thread.id, turn.id, scope=arguments.get("scope", ""), reason="read-only review mode"
        )
        return json.dumps({"granted": False, "reason": "read-only review mode"})

    scope = arguments.get("scope", "")
    reason = arguments.get("reason", "")
    duration = arguments.get("duration", "turn")
    if scope not in ("network", "write_outside_cwd", "full_access"):
        return json.dumps({"granted": False, "reason": f"invalid scope: {scope}"})

    summary = format_tool_summary("request_permissions", arguments)
    if not config.auto_approve and needs_approval_prompt(
        "request_permissions", arguments, config, turn_state=turn_state, session=session
    ):
        emitter.approval_requested(thread.id, turn.id, "request_permissions", summary)
        if not prompt_approval(
            "request_permissions",
            arguments,
            auto_approve=config.auto_approve,
            turn_state=turn_state,
            session=session,
        ):
            emitter.permission_denied(thread.id, turn.id, scope=scope, reason="user denied")
            return json.dumps({"granted": False, "reason": "user denied"})

    session.grant_permission(scope, duration=duration)
    emitter.permission_escalated(
        thread.id, turn.id, scope=scope, duration=duration, reason=reason
    )
    return json.dumps({"granted": True, "scope": scope, "duration": duration})
