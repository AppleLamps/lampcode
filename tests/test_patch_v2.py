from pathlib import Path

import pytest

from tools.patch import apply_patch


MULTI_HUNK = """*** Begin Patch
*** Update File: calc.py
@@
 def add(a, b):
-    return a - b
+    return a + b
@@
 # footer
-# old
+# new
*** End Patch
"""


def test_multi_hunk_update(tmp_path: Path) -> None:
    f = tmp_path / "calc.py"
    f.write_text("def add(a, b):\n    return a - b\n# footer\n# old\n")
    outcome = apply_patch(tmp_path, MULTI_HUNK)
    assert outcome.ok
    text = f.read_text()
    assert "return a + b" in text
    assert "# new" in text


def test_context_line_matching(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("alpha\nbeta\ngamma\n")
    patch = """*** Begin Patch
*** Update File: x.py
@@
 alpha
-beta
+BRAVO
*** End Patch
"""
    outcome = apply_patch(tmp_path, patch)
    assert outcome.ok
    assert "BRAVO" in f.read_text()


def test_error_includes_read_file_hint(tmp_path: Path) -> None:
    f = tmp_path / "calc.py"
    f.write_text("other\n")
    patch = """*** Begin Patch
*** Update File: calc.py
@@
-missing line
+new
*** End Patch
"""
    outcome = apply_patch(tmp_path, patch)
    assert not outcome.ok
    assert "read_file" in (outcome.error or "")


def test_malformed_patch_section() -> None:
    with pytest.raises(ValueError):
        from tools.patch import parse_patch

        parse_patch("*** Begin Patch\n*** Bad Section: x\n*** End Patch")
