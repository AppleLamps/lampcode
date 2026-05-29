from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.models import Item, Thread, Turn, new_id, parse_item, utc_now_iso
from agent.store_lock import acquire_file_lock


def default_store_dir() -> Path:
    return Path.home() / ".agent-cli" / "threads"


@dataclass
class ThreadStoreSettings:
    lock_timeout_sec: float = 5.0
    metadata_sidecar: bool = True
    compact_on_rewrite: bool = True


class ThreadStore:
    def __init__(
        self,
        base_dir: Path | None = None,
        *,
        persistent: bool = True,
        settings: ThreadStoreSettings | None = None,
    ) -> None:
        self.persistent = persistent
        self.base_dir = base_dir or default_store_dir()
        self.settings = settings or ThreadStoreSettings()
        if self.persistent:
            self.base_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def ephemeral(cls) -> ThreadStore:
        import tempfile

        return cls(base_dir=Path(tempfile.mkdtemp(prefix="agent-cli-ephemeral-")), persistent=False)

    def thread_path(self, thread_id: str) -> Path:
        return self.base_dir / f"{thread_id}.jsonl"

    def thread_meta_path(self, thread_id: str) -> Path:
        return self.base_dir / f"{thread_id}.meta.json"

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
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            if not path.exists():
                self._write_records_atomic(path, [{"record_type": "meta", "thread": self._thread_meta(thread)}])
            else:
                self.write_thread_meta(thread)

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
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            if not path.exists():
                self._append_record(path, {"record_type": "meta", "thread": self._thread_meta(thread)})
            self._append_record(path, record)
            self.write_thread_meta(thread)

    def append_turn(self, thread: Thread, turn: Turn) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        thread.touch()
        record = {
            "record_type": "turn",
            "turn": turn.model_dump(),
        }
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            if not path.exists():
                self._append_record(path, {"record_type": "meta", "thread": self._thread_meta(thread)})
            self._append_record(path, record)
            self.write_thread_meta(thread)

    def create_thread(self, thread: Thread) -> None:
        if not self.persistent:
            return
        path = self.thread_path(thread.id)
        meta = {"record_type": "meta", "thread": self._thread_meta(thread)}
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            self._write_records_atomic(path, [meta])
            self.write_thread_meta(thread)

    def load_thread(self, thread_id: str) -> Thread:
        path = self.thread_path(thread_id)
        if not path.exists():
            raise FileNotFoundError(f"Thread not found: {thread_id}")

        records = self._read_records(path)
        meta = next((r for r in records if r.get("record_type") == "meta"), None)
        if not meta:
            raise ValueError(f"Corrupt thread file (missing meta): {thread_id}")

        sidecar_thread = None
        meta_path = self.thread_meta_path(thread_id)
        if self.settings.metadata_sidecar and meta_path.exists():
            sidecar_thread = self._read_thread_meta_from_sidecar(meta_path)
        thread_data = sidecar_thread.model_dump() if sidecar_thread is not None else meta["thread"]
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
        meta_path = self.thread_meta_path(thread_id)
        if self.settings.metadata_sidecar and meta_path.exists():
            thread = self._read_thread_meta_from_sidecar(meta_path)
            if thread is not None:
                return thread
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

    def _read_thread_meta_from_sidecar(self, path: Path) -> Thread | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            thread_data = data.get("thread", data)
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
        paths = list(self.base_dir.glob("*.jsonl"))
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths:
            thread = None
            meta_path = self.thread_meta_path(path.stem)
            if self.settings.metadata_sidecar and meta_path.exists():
                thread = self._read_thread_meta_from_sidecar(meta_path)
            if thread is None:
                thread = self._read_thread_meta_from_path(path)
            if thread is not None:
                threads.append(thread)
        return sorted(threads, key=lambda t: t.updated_at, reverse=True)

    def find_matches_by_prefix(self, thread_id_or_prefix: str, *, meta_only: bool = True) -> list[Thread]:
        threads = self.list_thread_meta() if meta_only else self.list_threads()
        return [
            thread
            for thread in threads
            if thread.id == thread_id_or_prefix or thread.id.startswith(thread_id_or_prefix)
        ]

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
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            path.unlink()
            meta_path = self.thread_meta_path(thread_id)
            if meta_path.exists():
                meta_path.unlink()
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
        records = [meta]
        for turn in thread.turns:
            records.append({"record_type": "turn", "turn": turn.model_dump()})
            for item in turn.items:
                records.append(
                    {
                        "record_type": "item",
                        "turn_id": turn.id,
                        "item": item.model_dump(),
                    }
                )
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            self._write_records_atomic(path, records)
            self.write_thread_meta(thread)

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
        with acquire_file_lock(path, timeout_sec=self.settings.lock_timeout_sec):
            self.write_thread_meta(thread)

    def write_thread_meta(self, thread: Thread) -> None:
        if not self.persistent or not self.settings.metadata_sidecar:
            return
        meta_path = self.thread_meta_path(thread.id)
        payload = {"record_type": "meta", "thread": self._thread_meta(thread)}
        self._write_json_atomic(meta_path, payload)

    def _append_record(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.write("\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)

    def _write_records_atomic(self, path: Path, records: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
