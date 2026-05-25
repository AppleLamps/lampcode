from approval.gate import TurnApprovalState, classify_command, prompt_approval


def test_classify_read_command() -> None:
    assert classify_command("cat file.txt") == "read"
    assert classify_command("Get-Content x.py") == "read"


def test_classify_test_command() -> None:
    assert classify_command("pytest -q") == "test/run"


def test_classify_write_command() -> None:
    assert classify_command("rm -rf build") == "write/destructive"


def test_approve_all_for_turn() -> None:
    state = TurnApprovalState()
    state.approve_all = True
    assert prompt_approval(
        "run_command",
        {"cmd": "echo hi"},
        turn_state=state,
    )
