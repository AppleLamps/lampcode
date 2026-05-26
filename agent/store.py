from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.models import Item, Thread, Turn, new_id, parse_item, utc_now_iso


def default_store_dir() -> Path:
    return Path.home() / ".agent-cli" / "threads"


class ThreadStore:
    def __init__(self, base_dir: Path | None = None, *, persistent: bool = True) -> None:
        self.persistent = persistent
        self.base_dir = base_dir or default_store_dir()
        if self.persistent:
            self.base_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def ephemeral(cls) -> ThreadStore:
        import tempfile

        return cls(base_dir=Path(tempfile.mkdtemp(prefix="agent-cli-ephemeral-")), persistent=False)

    def thread_path(self, thread_id: str) -> Path:
        return self.base_dir / f"{thread_id}.jsonl"

    def _thread_meta(self, thread: Thread) -> dict[str, Any]:
        return {
            "id": thread.id,
            "cwd": thread.cwd,
            "model": thread.model,
            "repo_root": thread.repo_root,
            "forked_from": thread.forked_from,
            "title": thread.title,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
        }

    def save_thread(self, thread: Thread) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        thread.touch()
        records: list[dict[str, Any]] = []

        if path.exists():
            records = self._read_records(path)
            records = [
                r
                for r in records
                if r.get("record_type") not in ("meta",)
            ]

        meta = {"record_type": "meta", "thread": self._thread_meta(thread)}
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")
            for record in records:
                f.write(json.dumps(record) + "\n")

    def append_item(self, thread: Thread, turn_id: str, item: Item) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        thread.touch()
        record = {
            "record_type": "item",
            "turn_id": turn_id,
            "item": item.model_dump(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self._update_meta_timestamp(thread)

    def append_turn(self, thread: Thread, turn: Turn) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        thread.touch()
        record = {
            "record_type": "turn",
            "turn": turn.model_dump(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        self._update_meta_timestamp(thread)

    def create_thread(self, thread: Thread) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        meta = {"record_type": "meta", "thread": self._thread_meta(thread)}
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")

    def load_thread(self, thread_id: str) -> Thread:
        path = self.thread_path(thread_id)
        if not path.exists():
            raise FileNotFoundError(f"Thread not found: {thread_id}")

        records = self._read_records(path)
        meta = next((r for r in records if r.get("record_type") == "meta"), None)
        if not meta:
            raise ValueError(f"Corrupt thread file (missing meta): {thread_id}")

        thread_data = meta["thread"]
        thread = Thread(
            id=thread_data["id"],
            cwd=thread_data["cwd"],
            model=thread_data["model"],
            repo_root=thread_data.get("repo_root"),
            forked_from=thread_data.get("forked_from"),
            title=thread_data.get("title"),
            created_at=thread_data["created_at"],
            updated_at=thread_data["updated_at"],
            turns=[],
        )

        turns_by_id: dict[str, Turn] = {}
        turn_order: list[str] = []
        for record in records:
            if record.get("record_type") == "turn":
                turn = Turn.model_validate(record["turn"])
                if turn.id not in turns_by_id:
                    turn_order.append(turn.id)
                turns_by_id[turn.id] = turn

        items_by_turn: dict[str, dict[str, Item]] = {}
        for record in records:
            if record.get("record_type") == "item":
                turn_id = record["turn_id"]
                item = parse_item(record["item"])
                if item is None:
                    continue
                if turn_id not in items_by_turn:
                    items_by_turn[turn_id] = {}
                items_by_turn[turn_id][item.id] = item
                if turn_id not in turns_by_id:
                    turns_by_id[turn_id] = Turn(id=turn_id)
                    turn_order.append(turn_id)

        for turn_id in turn_order:
            turn = turns_by_id[turn_id]
            if turn_id in items_by_turn:
                turn.items = list(items_by_turn[turn_id].values())

        thread.turns = [turns_by_id[tid] for tid in turn_order if tid in turns_by_id]
        return thread

    def read_thread_meta(self, thread_id: str) -> Thread:
        """Load thread header only (first meta record) — fast for pickers and startup."""
        path = self.thread_path(thread_id)
        if not path.exists():
            raise FileNotFoundError(f"Thread not found: {thread_id}")
        thread = self._read_thread_meta_from_path(path)
        if thread is None:
            raise ValueError(f"Corrupt thread file (missing meta): {thread_id}")
        return thread

    def _read_thread_meta_from_path(self, path: Path) -> Thread | None:
        try:
            with path.open("r", encoding="utf-8") as f:
                first = f.readline().strip()
            if not first:
                return None
            record = json.loads(first)
            if record.get("record_type") != "meta":
                return None
            thread_data = record["thread"]
            return Thread(
                id=thread_data["id"],
                cwd=thread_data["cwd"],
                model=thread_data["model"],
                repo_root=thread_data.get("repo_root"),
                forked_from=thread_data.get("forked_from"),
                title=thread_data.get("title"),
                created_at=thread_data["created_at"],
                updated_at=thread_data["updated_at"],
                turns=[],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def list_thread_meta(self) -> list[Thread]:
        """List threads using only each file's meta line (no turn/item parse)."""
        threads: list[Thread] = []
        for path in sorted(
            self.base_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            thread = self._read_thread_meta_from_path(path)
            if thread is not None:
                threads.append(thread)
        return threads

    def list_threads(self) -> list[Thread]:
        threads: list[Thread] = []
        for path in sorted(self.base_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                thread_id = path.stem
                threads.append(self.load_thread(thread_id))
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
        return threads

    def _read_records(self, path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def delete_thread(self, thread_id: str) -> None:
        path = self.thread_path(thread_id)
        if not path.exists():
            raise FileNotFoundError(f"Thread not found: {thread_id}")
        path.unlink()
        try:
            from agent.multi_agent.dag_state import clear_dag_state_for_thread

            clear_dag_state_for_thread(thread_id)
        except Exception:
            pass

    def rewrite_turns(self, thread: Thread) -> None:
        """Replace turn/item records while preserving meta (used after compaction)."""
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        thread.touch()
        meta = {"record_type": "meta", "thread": self._thread_meta(thread)}
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")
            for turn in thread.turns:
                f.write(
                    json.dumps({"record_type": "turn", "turn": turn.model_dump()})
                    + "\n"
                )
                for item in turn.items:
                    f.write(
                        json.dumps(
                            {
                                "record_type": "item",
                                "turn_id": turn.id,
                                "item": item.model_dump(),
                            }
                        )
                        + "\n"
                    )

    def find_latest_for_cwd(self, cwd: str) -> Thread | None:
        target = str(Path(cwd).resolve())
        matches = [
            thread
            for thread in self.list_thread_meta()
            if str(Path(thread.cwd).resolve()) == target
        ]
        if not matches:
            return None
        return max(matches, key=lambda thread: thread.updated_at)

    def fork_thread(self, source: Thread, title: str | None = None) -> Thread:
        forked = source.model_copy(deep=True)
        forked.id = new_id()
        forked.forked_from = source.id
        forked.title = title or (source.title and f"{source.title} (fork)")
        forked.created_at = utc_now_iso()
        forked.updated_at = utc_now_iso()
        for turn in forked.turns:
            turn.id = new_id()
            for item in turn.items:
                item.id = new_id()
        self.rewrite_turns(forked)
        return forked

    def rename_thread(self, thread: Thread, title: str) -> None:
        thread.title = title
        self.save_thread(thread)

    def _update_meta_timestamp(self, thread: Thread) -> None:
        path = self.thread_path(thread.id)
        records = self._read_records(path)
        for record in records:
            if record.get("record_type") == "meta":
                record["thread"] = self._thread_meta(thread)
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
