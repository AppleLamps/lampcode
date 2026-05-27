from pathlib import Path

from agent.context import build_system_prompt
from tools.code_intel import (
    file_imports,
    file_outline,
    find_references,
    go_to_definition,
)
from tools.registry import TOOL_REGISTRY, dispatch_tool


SAMPLE_PY = '''"""module doc"""
import os
from pathlib import Path

def add(a, b):
    return a + b

class Calculator:
    def mul(self, x, y):
        return x * y
'''

OTHER_PY = """from sample import add

def use_add():
    return add(1, 2)
"""


def test_file_outline_python(tmp_path: Path) -> None:
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_PY, encoding="utf-8")
    out = file_outline(tmp_path, "sample.py")
    assert "add" in out
    assert "Calculator" in out
    assert "Calculator.mul" in out or "mul" in out


def test_go_to_definition_with_hint(tmp_path: Path) -> None:
    f = tmp_path / "sample.py"
    f.write_text(SAMPLE_PY, encoding="utf-8")
    out = go_to_definition(tmp_path, "add", path_hint="sample.py")
    assert "sample.py" in out
    assert "add" in out


def test_find_references(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(SAMPLE_PY, encoding="utf-8")
    (tmp_path / "other.py").write_text(OTHER_PY, encoding="utf-8")
    out = find_references(tmp_path, "add", prefer_ripgrep=False)
    assert "other.py" in out or "sample.py" in out


def test_file_imports_python(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text("from pkg.util import VALUE\n", encoding="utf-8")
    out = file_imports(tmp_path, "main.py")
    assert "pkg" in out


def test_registry_dispatch_outline(tmp_path: Path) -> None:
    from agent.config import Config

    (tmp_path / "x.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    cfg = Config(cwd=tmp_path, model="test", openrouter_api_key="k")
    result = dispatch_tool("file_outline", {"path": "x.py"}, cfg)
    assert "foo" in result.text


def test_registry_has_code_intel_tools() -> None:
    for name in (
        "file_outline",
        "go_to_definition",
        "find_references",
        "file_imports",
    ):
        assert name in TOOL_REGISTRY


def test_system_prompt_documents_code_nav_tools(tmp_path: Path) -> None:
    prompt = build_system_prompt(tmp_path, None)
    assert "file_outline" in prompt
    assert "go_to_definition" in prompt
    assert "find_references" in prompt
