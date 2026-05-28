from __future__ import annotations

import pytest

from agent.user_input import set_user_input_handler
from approval.gate import set_approval_input, set_http_approval_bridge


@pytest.fixture(autouse=True)
def _reset_approval_globals() -> None:
    set_approval_input(None)
    set_user_input_handler(None)
    set_http_approval_bridge(None)
    yield
    set_approval_input(None)
    set_user_input_handler(None)
    set_http_approval_bridge(None)


@pytest.fixture(autouse=True)
def _reset_turn_harness_singletons() -> None:
    yield
    from agent.harness.active_turns import ActiveTurnRegistry
    from agent.multi_agent.registry import WorkerRegistry
    from agent.serve.turn_runner import TurnRunner

    ActiveTurnRegistry.global_registry().cancel_all()
    WorkerRegistry.reset_for_tests()
    ActiveTurnRegistry.reset_for_tests()
    TurnRunner.reset_for_tests()
