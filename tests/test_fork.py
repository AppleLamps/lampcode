from pathlib import Path

from agent.models import AgentMessageItem, Thread, Turn, UserMessageItem
from agent.store import ThreadStore


def test_fork_preserves_items_and_sets_lineage(tmp_path: Path) -> None:
    source = Thread(id="source-thread-id-12345678", cwd=str(tmp_path), model="test")
    turn = Turn()
    turn.items = [
        UserMessageItem(text="hello"),
        AgentMessageItem(text="world"),
    ]
    source.turns.append(turn)

    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(source)
    store.append_turn(source, turn)
    for item in turn.items:
        store.append_item(source, turn.id, item)

    loaded = store.load_thread(source.id)
    forked = store.fork_thread(loaded, title="experiment")

    assert forked.id != source.id
    assert forked.forked_from == source.id
    assert forked.title == "experiment"
    assert len(forked.turns) == 1
    assert len(forked.turns[0].items) == 2
    assert forked.turns[0].items[0].text == "hello"

    original = store.load_thread(source.id)
    assert len(original.turns) == 1
    assert original.turns[0].items[0].text == "hello"


def test_rename_thread(tmp_path: Path) -> None:
    thread = Thread(id="rename-test-id-12345678", cwd=str(tmp_path), model="test")
    store = ThreadStore(base_dir=tmp_path / "threads")
    store.create_thread(thread)
    store.rename_thread(thread, "bug fix")
    reloaded = store.load_thread(thread.id)
    assert reloaded.title == "bug fix"


def test_display_label_fork_lineage() -> None:
    thread = Thread(
        id="abc123def456",
        cwd="/tmp",
        model="test",
        title="main",
        forked_from="parent-thread-id-999",
    )
    label = thread.display_label()
    assert "main" in label
    assert "fork of parent-t" in label
