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
