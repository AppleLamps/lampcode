from pathlib import Path

import pytest

from tools.patch import apply_patch, parse_patch


UPDATE_PATCH = """*** Begin Patch
*** Update File: calc.py
@@
-    return a - b
+    return a + b
*** End Patch
"""

ADD_PATCH = """*** Begin Patch
*** Add File: new.py
+line1
+line2
*** End Patch
"""

DELETE_PATCH = """*** Begin Patch
*** Delete File: old.py
*** End Patch
"""


def test_update_hunk(tmp_path: Path) -> None:
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a - b\n")
    outcome = apply_patch(tmp_path, UPDATE_PATCH)
    assert outcome.ok
    assert "updated" in outcome.results[0].summary
    assert "return a + b" in f.read_text()


def test_add_file(tmp_path: Path) -> None:
    outcome = apply_patch(tmp_path, ADD_PATCH)
    assert outcome.ok
    assert (tmp_path / "new.py").read_text() == "line1\nline2\n"


def test_delete_file(tmp_path: Path) -> None:
    f = tmp_path / "old.py"
    f.write_text("x\n")
    outcome = apply_patch(tmp_path, DELETE_PATCH)
    assert outcome.ok
    assert not f.exists()


def test_reject_path_traversal(tmp_path: Path) -> None:
    patch = """*** Begin Patch
*** Add File: ../escape.py
+bad
*** End Patch
"""
    outcome = apply_patch(tmp_path, patch)
    assert not outcome.ok
    assert "escapes" in (outcome.error or "").lower()


def test_reject_malformed_patch(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        parse_patch("not a patch")


def test_missing_update_line(tmp_path: Path) -> None:
    f = tmp_path / "calc.py"
    f.write_text("other content\n")
    outcome = apply_patch(tmp_path, UPDATE_PATCH)
    assert not outcome.ok
