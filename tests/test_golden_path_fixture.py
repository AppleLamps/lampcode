from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PROJECT = REPO_ROOT / "examples" / "demo-project"


def test_demo_project_fixture_files() -> None:
    assert DEMO_PROJECT.is_dir()
    assert (DEMO_PROJECT / "calc.py").is_file()
    assert (DEMO_PROJECT / "test_calc.py").is_file()
    assert (DEMO_PROJECT / "AGENTS.md").is_file()
    assert (DEMO_PROJECT / "README.md").is_file()
    assert (DEMO_PROJECT / "setup.ps1").is_file()
    assert (DEMO_PROJECT / ".agent-cli" / "config.toml").is_file()
    assert (DEMO_PROJECT / ".agent-cli" / "skills" / "pytest-fix" / "SKILL.md").is_file()


def test_demo_project_readme_documents_golden_path() -> None:
    readme = (DEMO_PROJECT / "README.md").read_text(encoding="utf-8")
    assert "agent init" in readme
    assert "fix failing tests" in readme
    assert "setup.ps1" in readme


def test_demo_project_config_has_model_profiles() -> None:
    cfg = (DEMO_PROJECT / ".agent-cli" / "config.toml").read_text(encoding="utf-8")
    assert "[model_profiles.deep]" in cfg
    assert "[model_routing]" in cfg
