from __future__ import annotations

import queue
import threading

from agent.events import AgentEvent
from agent.tui.view_model import TuiState, apply_event_to_state
from agent.user_input import resolve_user_input, set_user_input_handler


def test_view_model_user_input_requested() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "user_input.requested",
            data={
                "question": "Which framework?",
                "options": ["Flask", "FastAPI"],
                "allow_free_text": False,
            },
        ),
    )
    assert state.pending_user_input_question == "Which framework?"
    assert state.pending_user_input_options == ["Flask", "FastAPI"]


def test_view_model_user_input_answer_cell() -> None:
    state = TuiState()
    state = apply_event_to_state(
        state,
        AgentEvent(
            "user.input",
            data={
                "question": "Pick one",
                "answer": "FastAPI",
                "selected_option": "FastAPI",
            },
        ),
    )
    assert state.pending_user_input_question is None
    assert any("FastAPI" in c.text for c in state.transcript if hasattr(c, "text"))


def test_user_input_handler_blocks_until_response() -> None:
    responses: queue.Queue[dict[str, str | None]] = queue.Queue()

    def handler(
        question: str,
        options: list[str] | None,
        allow_free: bool,
        index: int,
        total: int,
    ):
        assert question == "Color?"
        assert options == ["red", "blue"]
        assert index == 1 and total == 1
        payload = responses.get(timeout=2.0)
        return payload["answer"], payload.get("selected_option"), None

    set_user_input_handler(handler)

    def answer_later() -> None:
        responses.put({"answer": "blue", "selected_option": "blue"})

    threading.Timer(0.05, answer_later).start()
    ans, sel, err = resolve_user_input("Color?", ["red", "blue"], allow_free_text=False)
    assert err is None
    assert ans == "blue"
    assert sel == "blue"
    set_user_input_handler(None)
