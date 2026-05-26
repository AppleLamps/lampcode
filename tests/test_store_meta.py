from pathlib import Path

from agent.models import Thread, Turn, UserMessageItem
from agent.store import ThreadStore


def test_list_thread_meta_reads_only_header(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="t1", cwd=str(tmp_path), model="test-model", title="my task")
    store.create_thread(thread)
    turn = Turn()
    turn.items.append(UserMessageItem(text="hello"))
    store.append_turn(thread, turn)
    store.append_item(thread, turn.id, turn.items[0])

    meta_threads = store.list_thread_meta()
    assert len(meta_threads) == 1
    assert meta_threads[0].id == "t1"
    assert meta_threads[0].title == "my task"
    assert meta_threads[0].turns == []

    full = store.load_thread("t1")
    assert len(full.turns) == 1
    assert full.turns[0].items[0].text == "hello"


def test_read_thread_meta_matches_load_header(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="abc", cwd=str(tmp_path), model="m")
    store.create_thread(thread)
    meta = store.read_thread_meta("abc")
    assert meta.model == "m"
    assert meta.cwd == str(tmp_path)
