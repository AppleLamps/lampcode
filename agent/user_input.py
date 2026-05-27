from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

# TUI / harness hook: (question, options, allow_free_text, index, total) -> (answer, selected, error)
UserInputHandler = Callable[
    [str, list[str] | None, bool, int, int],
    tuple[str | None, str | None, str | None],
]

_user_input_handler: UserInputHandler | None = None


def set_user_input_handler(fn: UserInputHandler | None) -> None:
    global _user_input_handler
    _user_input_handler = fn


def normalize_user_input_questions(arguments: dict) -> list[dict[str, Any]]:
    """Expand tool args into a list of question specs (single or batch)."""
    raw = arguments.get("questions")
    if isinstance(raw, list) and raw:
        specs: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question", "")).strip()
            if not question:
                continue
            opts = item.get("options")
            if opts is not None and not isinstance(opts, list):
                opts = None
            specs.append(
                {
                    "question": question,
                    "options": [str(o) for o in opts] if opts else None,
                    "allow_free_text": bool(item.get("allow_free_text", True)),
                }
            )
        if specs:
            return specs

    question = str(arguments.get("question", "")).strip()
    if not question:
        return []
    opts = arguments.get("options")
    if opts is not None and not isinstance(opts, list):
        opts = None
    return [
        {
            "question": question,
            "options": [str(o) for o in opts] if opts else None,
            "allow_free_text": bool(arguments.get("allow_free_text", True)),
        }
    ]


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
    question_index: int = 1,
    question_total: int = 1,
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

    if _user_input_handler is not None:
        return _user_input_handler(
            question, options, allow_free_text, question_index, question_total
        )

    if input_fn:
        header = ""
        if question_total > 1:
            header = f"[{question_index}/{question_total}]\n"
        prompt_lines = [header + question]
        if options:
            for i, opt in enumerate(options, 1):
                prompt_lines.append(f"  {i}. {opt}")
            prompt_lines.append("Enter number:")
        else:
            prompt_lines.append(">")
        prompt = "\n".join(prompt_lines) + "\n"
        raw = input_fn(prompt).strip()
        if options and raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx], options[idx], None
        if not raw:
            return None, None, "empty response"
        return raw, None, None

    is_tty = sys.stdin.isatty() and sys.stdout.isatty()
    if headless_json or not is_tty:
        return (
            None,
            None,
            "request_user_input requires TTY, --auto-approve, or AGENT_INPUT_ANSWERS JSON",
        )

    prompt_lines = []
    if question_total > 1:
        prompt_lines.append(f"[{question_index}/{question_total}]")
    prompt_lines.append(question)
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
