from agent.export.html import export_thread_html, render_index_html
from agent.models import CollabWorkerItem, CommandExecutionItem, Thread, Turn, UserMessageItem, WorkspaceSyncItem
from agent.multi_agent.checkpoint import SupervisorCheckpoint, WorkerCheckpoint


def test_render_index_html() -> None:
    threads = [
        Thread(id="abc12345", cwd="/tmp", model="m", title="demo"),
    ]
    html = render_index_html(threads)
    assert "<h1>Threads</h1>" in html
    assert "abc12345" in html
    assert "demo" in html


def test_export_thread_html_snapshot() -> None:
    thread = Thread(id="t1", cwd="/tmp/proj", model="test", title="fix")
    turn = Turn(status="completed")
    turn.items = [
        UserMessageItem(text="hello"),
        CommandExecutionItem(
            command="pytest",
            cwd="/tmp/proj",
            status="completed",
            backend="ssh",
            remote_host="devbox",
            remote_user="ubuntu",
        ),
        CollabWorkerItem(
            worker_id="w-1",
            worker_thread_id="wt-1",
            parent_thread_id="t1",
            task="run tests",
            depth=0,
            status="completed",
            summary="tests pass",
        ),
    ]
    thread.turns.append(turn)
    html = export_thread_html(thread, sandbox="workspace-write", backend="ssh")
    assert "<!DOCTYPE html>" in html
    assert "fix" in html
    assert "pytest" in html
    assert "w-1" in html
    assert "tests pass" in html
    assert "ssh" in html.lower() or "Command" in html


def test_export_thread_html_sync_and_checkpoint() -> None:
    thread = Thread(id="t1", cwd="/tmp/proj", model="test", title="sync-demo")
    turn = Turn(status="completed")
    turn.items = [
        WorkspaceSyncItem(
            direction="push",
            transport="rsync",
            status="completed",
            summary="42 files, 1.2 MB",
        ),
    ]
    thread.turns.append(turn)
    cp = SupervisorCheckpoint(
        thread_id="t1",
        turn_id="turn-abc",
        workers=[
            WorkerCheckpoint(
                worker_id="w-1",
                parent_thread_id="t1",
                task="lint",
                depth=0,
                status="completed",
            ),
            WorkerCheckpoint(
                worker_id="w-2",
                parent_thread_id="t1",
                task="tests",
                depth=0,
                status="running",
            ),
        ],
    )
    html = export_thread_html(thread, backend="ssh", checkpoint=cp)
    assert "Last sync" in html
    assert "Latest checkpoint" in html
    assert "w-2" in html
