# agent-cli vs official Codex CLI

agent-cli is a **Python harness** inspired by OpenAI Codex CLI — same mental model (terminal agent, tools, threads), different runtime and provider stack.

## What we match

| Area | Codex CLI | agent-cli |
|------|-----------|-----------|
| Core loop | Prompt → model → tools → repeat | Same (`agent run`, `agent repl`, `agent tui`) |
| Config layering | Global + project `config.toml` | `~/.agent-cli/config.toml` + `{cwd}/.agent-cli/config.toml` |
| Sandbox modes | read-only / workspace-write / full | Same three modes + exec policy allow/deny |
| Approvals | y / n / session | y / n / a (turn) / A (session) |
| Threads | List, resume, fork | `agent threads list|show|fork|rename|cost|delete` |
| Tool surface | read, search, patch, shell | `read_file`, `search_repo`, `apply_patch`, `write_file`, `run_command`, optional `git_commit` |
| MCP | Supported | Supported (`mcp__{server}__{tool}`) |
| Skills / rules | AGENTS.md-style guidance | Project rules + `SKILL.md` skills (auto-select, max 3/turn) |
| Recording | Session logs | JSONL runs under `~/.agent-cli/runs/` + `agent runs replay` |
| Interactive UI | TUI | Optional Textual TUI (`pip install -e ".[tui]"`) |
| Model profiles | Named profiles in config | `[model_profiles.*]` + task routing (`[model_routing]`) |
| Init scaffold | Project bootstrap | `agent init` (git repo → `.agent-cli/`, `AGENTS.md`) |

## What we do differently (by design)

| Area | Codex CLI | agent-cli |
|------|-----------|-----------|
| Runtime | Rust binary | Python 3.11+ |
| Models | OpenAI-only | **Any OpenRouter model** + configurable fallbacks |
| API key | OpenAI | `OPENROUTER_API_KEY` (`.env` auto-loaded) |
| Cost | Limited visibility | Per-turn USD estimate + `agent threads cost` |
| Provider errors | Single vendor | Retry + fallback chain (`fallback_on`, `fallback_models`) |
| Unsupported models | N/A | Warns when model may lack tool calling (`openrouter/auto`, etc.) |
| Remote execution | Codex cloud / local | Optional Docker, SSH, workspace sync (see [enterprise.md](enterprise.md)) |
| Team serve | N/A in OSS Codex | Opt-in HTTP serve, OIDC, RBAC (see [enterprise.md](enterprise.md)) |

## What we do not match (yet)

These are **not** goals for v2.1.0 solo release:

- Full LSP bridge in the web IDE (Phase 21+)
- Linux bubblewrap policy packs as default on all distros
- Distributed schedule leader election
- Native Slack/email notification integrations
- Real-time websocket program sync
- Codex cloud sandbox / OpenAI Responses API parity

## When to pick which

**Use official Codex CLI** if you want the OpenAI-native Rust binary, Codex cloud execution, and zero Python dependency.

**Use agent-cli** if you want OpenRouter (model choice, fallbacks, cost estimates), a hackable Python codebase, optional team serve/OIDC behind `agent serve`, and the demo-project golden path (`examples/demo-project`).

## Quick parity checklist

```powershell
# Codex-like solo loop
agent init
agent doctor
agent run "fix failing tests" --model-profile deep
agent repl
agent threads cost <thread-id>

# Extras Codex OSS does not ship
agent models list
agent models recommend --task "fix tests"
agent skills doctor
agent threads pr-description <thread-id>
```

See [README](../README.md) for install and [enterprise.md](enterprise.md) for serve/OIDC/RBAC/DAG/scheduler.
