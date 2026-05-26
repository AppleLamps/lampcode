"""Tests for assistant streaming holdback."""

from agent.tui.streaming_controller import AssistantStreamController


def test_streaming_holds_incomplete_fence() -> None:
    ctrl = AssistantStreamController()
    assert ctrl.absorb("Here is code:\n```py\nprint(") == "Here is code:\n"
    assert ctrl.absorb("Here is code:\n```py\nprint(1)\n```") == "```py\nprint(1)\n```"


def test_streaming_flush_remainder() -> None:
    ctrl = AssistantStreamController()
    ctrl.absorb("```py\nx")
    tail = ctrl.flush_remainder("```py\nx")
    assert "```" in tail or "py" in tail
