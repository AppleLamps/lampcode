from agent.session import HarnessSession
from approval.gate import prompt_approval


def test_session_auto_approve_capital_a(monkeypatch) -> None:
    session = HarnessSession()
    monkeypatch.setattr("builtins.input", lambda: "A")
    assert prompt_approval("run_command", {"cmd": "echo"}, session=session)
    assert session.session_auto_approve


def test_session_banner_flag() -> None:
    session = HarnessSession()
    session.enable_session_auto_approve()
    assert session.session_auto_approve is True
    assert session.session_banner_shown is False
