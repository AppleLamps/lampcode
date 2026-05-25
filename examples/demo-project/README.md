# Demo Project

Intentionally broken `calc.add` for testing the agent.

```bash
pytest -q  # fails
```

Fix with:

```bash
agent run "Find why tests fail and fix them" --cwd examples/demo-project
```
