from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.models import Item, Thread, Turn, parse_item, utc_now_iso


def default_store_dir() -> Path:
    return Path.home() / ".agent-cli" / "threads"


class ThreadStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or default_store_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def thread_path(self, thread_id: str) -> Path:
        return self.base_dir / f"{thread_id}.jsonl"

    def save_thread(self, thread: Thread) -> None:
        path = self.thread_path(thread.id)
        thread.touch()
        records: list[dict[str, Any]] = []

        if path.exists():
            records = self._read_records(path)

        meta_records = [r for r in records if r.get("record_type") == "meta"]
        if meta_records:
            records = [r for r in records if r.get("record_type") != "meta"]

        meta = {
            "record_type": "meta",
            "thread": {
                "id": thread.id,
                "cwd": thread.cwd,
                "model": thread.model,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
            },
        }
        with path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta) + "\n")
            for record in records:
                f.write(json.dumps(record) + "\n")

    def append_item(self, thread: Thread, turn_id: str, item: Item) -> None:
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
        path = self.thread_path(thread.id)
        meta = {
            "record_type": "meta",
            "thread": {
                "id": thread.id,
                "cwd": thread.cwd,
                "model": thread.model,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
            },
        }
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

    def _update_meta_timestamp(self, thread: Thread) -> None:
        path = self.thread_path(thread.id)
        records = self._read_records(path)
        for record in records:
            if record.get("record_type") == "meta":
                record["thread"]["updated_at"] = thread.updated_at
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
