from pathlib import Path

from agent.context import find_project_rules, load_project_rules


def test_find_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Rules\nBe careful.", encoding="utf-8")
    rules = find_project_rules(tmp_path)
    assert rules is not None
    assert rules.path.name == "AGENTS.md"


def test_find_agents_lowercase(tmp_path: Path) -> None:
    (tmp_path / "agents.md").write_text("rules", encoding="utf-8")
    rules = find_project_rules(tmp_path)
    assert rules is not None


def test_find_dot_agents(tmp_path: Path) -> None:
    agents = tmp_path / ".agents"
    agents.mkdir()
    (agents / "AGENTS.md").write_text("nested rules", encoding="utf-8")
    rules = find_project_rules(tmp_path)
    assert rules is not None
    assert ".agents" in str(rules.path)


def test_load_truncates(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 10000, encoding="utf-8")
    text, meta = load_project_rules(tmp_path, max_chars=100)
    assert meta is not None
    assert "Project Rules" in text
    assert "truncated" in text
