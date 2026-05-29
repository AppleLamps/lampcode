from __future__ import annotations

import json
import threading
from pathlib import Path

from agent.models import Thread, Turn, UserMessageItem
from agent.store import ThreadStore


def test_append_item_does_not_rewrite_jsonl_header(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="t1", cwd=str(tmp_path), model="m")
    store.create_thread(thread)
    before = store.thread_path(thread.id).read_text(encoding="utf-8").splitlines()[0]
    turn = Turn()
    item = UserMessageItem(text="hello")
    store.append_item(thread, turn.id, item)
    after_lines = store.thread_path(thread.id).read_text(encoding="utf-8").splitlines()
    assert after_lines[0] == before
    assert len(after_lines) == 2
    assert store.thread_meta_path(thread.id).exists()


def test_list_thread_meta_prefers_sidecar(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="t1", cwd=str(tmp_path), model="m", title="old")
    store.create_thread(thread)
    meta = json.loads(store.thread_meta_path(thread.id).read_text(encoding="utf-8"))
    meta["thread"]["title"] = "sidecar"
    store.thread_meta_path(thread.id).write_text(json.dumps(meta), encoding="utf-8")
    listed = store.list_thread_meta()
    assert listed[0].title == "sidecar"


def test_prefix_matching_uses_metadata() -> None:
    store = ThreadStore(base_dir=Path("unused"), persistent=False)
    store.list_thread_meta = lambda: [Thread(id="abcdef", cwd=".", model="m")]  # type: ignore[method-assign]
    store.list_threads = lambda: (_ for _ in ()).throw(AssertionError("should not load full threads"))  # type: ignore[method-assign]
    matches = store.find_matches_by_prefix("abc", meta_only=True)
    assert len(matches) == 1
    assert matches[0].id == "abcdef"


def test_concurrent_append_does_not_corrupt_jsonl(tmp_path: Path) -> None:
    base = tmp_path / "threads"
    store = ThreadStore(base_dir=base)
    thread = Thread(id="t1", cwd=str(tmp_path), model="m")
    store.create_thread(thread)

    def append(i: int) -> None:
        local_store = ThreadStore(base_dir=base)
        local_store.append_item(thread, f"turn-{i}", UserMessageItem(text=f"msg {i}"))

    threads = [threading.Thread(target=append, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = [json.loads(line) for line in store.thread_path(thread.id).read_text(encoding="utf-8").splitlines()]
    assert records[0]["record_type"] == "meta"
    assert sum(1 for r in records if r.get("record_type") == "item") == 8


def test_atomic_rewrite_preserves_valid_file(tmp_path: Path) -> None:
    store = ThreadStore(base_dir=tmp_path / "threads")
    thread = Thread(id="t1", cwd=str(tmp_path), model="m")
    thread.turns.append(Turn(items=[UserMessageItem(text="hello")]))
    store.rewrite_turns(thread)
    loaded = store.load_thread(thread.id)
    assert loaded.turns[0].items[0].text == "hello"