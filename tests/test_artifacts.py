from __future__ import annotations

from agent.artifacts import artifact_path_for, read_artifact, spill_if_large


def test_spill_if_large_under_limit(tmp_path, monkeypatch):
    from agent import artifacts as art_mod

    monkeypatch.setattr(art_mod, "artifacts_root", lambda: tmp_path / "artifacts")
    monkeypatch.setattr(
        art_mod,
        "artifact_path_for",
        lambda *, thread_id, item_id: tmp_path / "artifacts" / thread_id / f"{item_id}.txt",
    )
    inline, path = spill_if_large("short", thread_id="t1", item_id="i1", inline_limit=100)
    assert inline == "short"
    assert path is None


def test_spill_if_large_writes_file(tmp_path, monkeypatch):
    from agent import artifacts as art_mod

    agent_home = tmp_path / "agent-cli"
    (agent_home / "artifacts").mkdir(parents=True)
    root = agent_home / "artifacts"

    def _path(*, thread_id, item_id):
        return root / thread_id / f"{item_id}.txt"

    monkeypatch.setattr(art_mod, "artifact_path_for", _path)
    monkeypatch.setattr(art_mod, "default_store_dir", lambda: agent_home / "threads")

    big = "line\n" * 500
    inline, rel = spill_if_large(big, thread_id="t1", item_id="i1", inline_limit=50)
    assert rel is not None
    assert "Full output:" in inline
    full_path = root / "t1" / "i1.txt"
    assert full_path.is_file()
    assert full_path.read_text(encoding="utf-8") == big


def test_read_artifact_rejects_escape(tmp_path, monkeypatch):
    from agent import artifacts as art_mod
    from agent.paths import default_store_dir

    base = tmp_path / "agent-cli"
    (base / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(art_mod, "default_store_dir", lambda: base / "threads")
    assert read_artifact("../../etc/passwd") is None
