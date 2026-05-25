from __future__ import annotations

import json
import os
import sys
from typing import Any


def load_ci_answers() -> dict[str, str]:
    raw = os.environ.get("AGENT_INPUT_ANSWERS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {}


def resolve_user_input(
    question: str,
    options: list[str] | None,
    *,
    allow_free_text: bool = True,
    auto_approve: bool = False,
    headless_json: bool = False,
    input_fn: Any = None,
    question_key: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Returns (answer, selected_option, error).
    selected_option is set when user picks from options list.
    """
    ci = load_ci_answers()
    key = question_key or question
    if key in ci:
        ans = ci[key]
        sel = ans if options and ans in options else None
        return ans, sel, None

    if auto_approve:
        if options:
            return options[0], options[0], None
        return "(auto-approved)", None, None

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if headless_json or not is_tty:
        return (
            None,
            None,
            "request_user_input requires TTY, --auto-approve, or AGENT_INPUT_ANSWERS JSON",
        )

    prompt_lines = [question]
    if options:
        for i, opt in enumerate(options, 1):
            prompt_lines.append(f"  {i}. {opt}")
        if allow_free_text:
            prompt_lines.append("Enter number or free text:")
        else:
            prompt_lines.append("Enter number:")
    else:
        prompt_lines.append(">")

    prompt = "\n".join(prompt_lines) + "\n"
    if input_fn:
        raw = input_fn(prompt).strip()
    else:
        try:
            print(prompt, end="", flush=True)
            raw = input().strip()
        except (EOFError, KeyboardInterrupt):
            return None, None, "user input cancelled"

    if options and raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(options):
            return options[idx], options[idx], None

    if not raw and not allow_free_text:
        return None, None, "free text not allowed"
    if not raw:
        return None, None, "empty response"
    return raw, None, None
