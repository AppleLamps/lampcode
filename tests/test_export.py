from agent.events import AgentEvent
from agent.export.markdown import export_run_markdown, export_thread_markdown
from agent.models import AgentMessageItem, CommandExecutionItem, Thread, Turn, UserMessageItem


def test_export_thread_markdown() -> None:
    thread = Thread(id="abc", cwd="/tmp/proj", model="test", title="fix bug")
    turn = Turn(status="completed")
    turn.items = [
        UserMessageItem(text="hello"),
        AgentMessageItem(text="world"),
        CommandExecutionItem(
            command="pytest",
            cwd="/tmp/proj",
            status="completed",
            backend="docker",
            image="python:3.12-slim",
        ),
    ]
    thread.turns.append(turn)
    md = export_thread_markdown(thread, sandbox="workspace-write", backend="docker")
    assert "# Thread" in md
    assert "fix bug" in md
    assert "**User:** hello" in md
    assert "docker" in md
    assert "pytest" in md


def test_export_run_markdown() -> None:
    events = [
        AgentEvent("turn.started", thread_id="t", turn_id="u"),
        AgentEvent("agent.delta", thread_id="t", turn_id="u", data={"text": "Hi"}),
        AgentEvent("turn.completed", thread_id="t", turn_id="u", data={"status": "completed"}),
    ]
    md = export_run_markdown(events)
    assert "# Run replay" in md
    assert "Hi" in md
