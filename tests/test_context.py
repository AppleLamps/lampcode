from agent.context import build_messages_from_turn_items
from agent.models import (
    AgentMessageItem,
    CommandExecutionItem,
    FileChangeItem,
    UserMessageItem,
)


def test_user_and_agent_messages() -> None:
    items = [
        UserMessageItem(text="hello"),
        AgentMessageItem(text="hi there"),
    ]
    messages = build_messages_from_turn_items(items)
    assert messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_tool_call_pairing() -> None:
    items = [
        UserMessageItem(text="run ls"),
        CommandExecutionItem(
            command="ls",
            cwd="/tmp",
            status="completed",
            output="file.txt",
            tool_call_id="call_1",
            tool_arguments='{"cmd": "ls"}',
        ),
        AgentMessageItem(text="Done."),
    ]
    messages = build_messages_from_turn_items(items)
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["id"] == "call_1"
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call_1"
    assert messages[3]["role"] == "assistant"


def test_denied_tool_result() -> None:
    items = [
        FileChangeItem(
            path="x.py",
            status="denied",
            tool_call_id="call_2",
            tool_arguments='{"path": "x.py", "content": "x"}',
            summary="User denied this action.",
        ),
    ]
    messages = build_messages_from_turn_items(items)
    assert messages[1]["content"] == "User denied this action."
