from pathlib import Path

from agent.context import build_system_prompt
from agent.init_scaffold import _build_init_config, init_project
from agent.project_context import (
    build_repo_map,
    detect_post_patch_test,
    load_project_context,
)


def test_detect_post_patch_test_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    assert detect_post_patch_test(tmp_path) == "pytest -q"


def test_detect_post_patch_test_npm(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run"}}',
        encoding="utf-8",
    )
    assert detect_post_patch_test(tmp_path) == "npm test"


def test_detect_post_patch_test_empty(tmp_path: Path) -> None:
    assert detect_post_patch_test(tmp_path) == ""


def test_load_project_context_includes_manifest_and_map(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print('hi')\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_x(): pass\n", encoding="utf-8")

    ctx = load_project_context(tmp_path)
    assert "README.md" in ctx
    assert "pyproject.toml" in ctx
    assert "Test layout" in ctx
    assert "src/" in ctx or "src/app.py" in ctx


def test_init_config_includes_detected_harness(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")

    text = _build_init_config("myproj", tmp_path)
    assert 'post_patch_test = "pytest -q"' in text
    assert "approval_mode = \"interactive\"" in text
    assert "[memories]" in text
    assert "enabled = true" in text
    assert "[web_search]" in text


def test_init_project_writes_interactive_config(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    init_project(tmp_path, yes=True)
    config = (tmp_path / ".agent-cli" / "config.toml").read_text(encoding="utf-8")
    assert "approval_mode = \"interactive\"" in config
    assert "post_patch_test" in config


def test_system_prompt_code_navigation_section(tmp_path: Path) -> None:
    prompt = build_system_prompt(tmp_path, None)
    assert "go_to_definition" in prompt
    assert "search_repo" in prompt


def test_build_repo_map_skips_node_modules(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x=1\n", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "pkg.js").write_text("", encoding="utf-8")
    text = build_repo_map(tmp_path)
    assert "src/" in text
    assert "node_modules" not in text
