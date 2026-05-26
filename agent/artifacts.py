"""Persist large tool outputs to disk; keep short summaries in thread items."""

from __future__ import annotations

from pathlib import Path

from agent.paths import default_store_dir


def artifacts_root() -> Path:
    return default_store_dir().parent / "artifacts"


def artifact_path_for(*, thread_id: str, item_id: str) -> Path:
    return artifacts_root() / thread_id / f"{item_id}.txt"


def spill_if_large(
    text: str,
    *,
    thread_id: str,
    item_id: str,
    inline_limit: int,
) -> tuple[str, str | None]:
    """Return (inline_text, artifact_relative_path | None)."""
    if not text or len(text) <= inline_limit:
        return text, None

    path = artifact_path_for(thread_id=thread_id, item_id=item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    lines = text.splitlines()
    preview_lines = lines[:12]
    preview = "\n".join(preview_lines)
    if len(lines) > 12:
        preview += f"\n... ({len(lines) - 12} more lines, {len(text):,} chars total)"
    rel = path.relative_to(default_store_dir().parent)
    inline = f"{preview}\n\nFull output: {rel.as_posix()}"
    return inline, str(rel.as_posix())


def read_artifact(relative_path: str) -> str | None:
    base = default_store_dir().parent
    path = (base / relative_path).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
