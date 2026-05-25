from pathlib import Path

from agent.models import Thread, Turn, UserMessageItem
from agent.store import ThreadStore


def test_delete_thread(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="abc", cwd=str(tmp_path), model="test")
    store.create_thread(thread)
    turn = Turn()
    turn.items.append(UserMessageItem(text="hi"))
    thread.turns.append(turn)
    store.append_item(thread, turn.id, turn.items[0])

    store.delete_thread("abc")
    assert not store.thread_path("abc").exists()


def test_find_latest_for_cwd(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    t1 = Thread(id="old", cwd=str(tmp_path), model="test")
    t2 = Thread(id="new", cwd=str(tmp_path), model="test")
    store.create_thread(t1)
    store.create_thread(t2)
    found = store.find_latest_for_cwd(str(tmp_path))
    assert found is not None
    assert found.id in ("old", "new")


def test_backward_compat_missing_repo_root(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    path = store.thread_path("legacy")
    path.write_text(
        '{"record_type": "meta", "thread": {"id": "legacy", "cwd": "'
        + str(tmp_path).replace("\\", "\\\\")
        + '", "model": "test", "created_at": "t", "updated_at": "t"}}\n'
    )
    thread = store.load_thread("legacy")
    assert thread.repo_root is None
