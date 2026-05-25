---
name: pytest-fix
description: Use when fixing or writing Python tests with pytest.
---

# Python Testing Skill

When fixing failing Python tests:

1. Run `pytest -q` to see failures.
2. Read the failing test file and the module under test.
3. Prefer minimal fixes with `apply_patch`.
4. Re-run pytest to confirm the fix.

Common issues:
- Off-by-one logic errors in arithmetic helpers
- Missing imports
- Incorrect assertions
