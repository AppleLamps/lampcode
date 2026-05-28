# Demo Project

Intentionally broken `calc.add` for testing the agent harness golden path. Optional: enable `[mcp_servers.lsp]` in `~/.agent-cli/config.toml` for Pyright-backed navigation — see [docs/code-navigation.md](../../docs/code-navigation.md).

```powershell
cd examples/demo-project
.\setup.ps1          # git init (once)
agent init --yes     # scaffold if needed
agent run "fix failing tests" --model-profile deep
pytest -q            # should pass after agent fixes calc.py
```

Manual check without setup script:

```powershell
git init
agent init --yes
agent run "fix failing tests" --model-profile deep
```
