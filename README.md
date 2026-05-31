# agent-cli

A **Codex-like coding agent harness** in Python, powered by [OpenRouter](https://openrouter.ai) — one API key, any model, fix your repo from the terminal.

## Quickstart (solo dev)

| Step | Command |
|------|---------|
| Install | `pip install -e ".[dev]"` |
| API key | `OPENROUTER_API_KEY=...` in `.env` (auto-loaded) or `$env:OPENROUTER_API_KEY = "..."` |
| Start chatting | `agent` (opens interactive TUI in current directory) |
| One-shot task | `agent run "fix failing tests"` |
| Plain terminal chat | `agent repl` |

```powershell
cd agent-cli
pip install -e ".[dev]"
# Option A: .env in repo root (loaded automatically)
# OPENROUTER_API_KEY=sk-or-v1-...
# Option B: shell env
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
cd examples\demo-project
.\setup.ps1
agent
agent run "fix failing tests" --model-profile deep
agent threads cost <thread-id>
```

**vs Codex CLI:** see [docs/codex-comparison.md](docs/codex-comparison.md). **Team serve / OIDC / RBAC / scheduler:** opt-in — [docs/enterprise.md](docs/enterprise.md).

## Install

```powershell
pip install -e ".[dev]"
```

Textual is included in core dependencies — `agent` opens the TUI by default.

## Configure

Precedence: CLI flags → env → `~/.agent-cli/config.toml` → `{cwd}/.agent-cli/config.toml` → defaults.

Put **LSP MCP** in global config once so every repo gets `mcp__lsp__*` without re-scaffolding:

```toml
# ~/.agent-cli/config.toml
[mcp_servers.lsp]
command = "agent"
args = ["lsp-mcp"]
enabled = true
require_approval = false

[plan_mode]
allow_mcp_servers = ["lsp"]
```

When `agent` connects this server, it pins `lsp-mcp` to the active project `cwd`.

See [docs/code-navigation.md](docs/code-navigation.md) and the [docs index](docs/README.md).

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
agent config show
agent doctor
agent doctor --deep   # optional MCP + Docker hello-world
```

Minimal project config (`agent init` creates this):

```toml
model = "minimax/minimax-m2.7"
approval_mode = "interactive"   # scaffold default; use "auto" for unattended runs
max_tool_rounds = 25
sandbox_mode = "workspace-write"
exec_policy = "untrusted"

[memories]
enabled = true
path = ".agent-cli/memories.json"

[web_search]
enabled = true

[harness]
post_patch_test = "pytest -q"   # auto-detected when possible (npm/cargo/go)
# lsp_diagnostics_after_patch = true   # optional; needs [mcp_servers.lsp]
# max_parallel_read_tools = 4

[execution]
# Defaults to local execution for compatibility. Use docker/ssh for stronger isolation.
backend = "local"
network = "none"

[execution.docker]
# Docker commands run with a read-only rootfs, dropped capabilities,
# no-new-privileges, and a non-root user by default.
read_only_rootfs = true
cap_drop_all = true
security_opt_no_new_privileges = true
user = "65532:65532"

[openrouter]
primary_model = "minimax/minimax-m2.7"
fallback_models = ["openai/gpt-4.1", "google/gemini-2.5-pro-preview"]
fallback_on = ["rate_limit", "provider_error", "timeout", "context_length"]
native_fallback = true
# max_tokens = 8192
# user_id = "my-user-id"
# reasoning_exclude = true

[openrouter.pricing."minimax/minimax-m2.7"]
input_per_million = 0.0
output_per_million = 0.0

[openrouter.pricing."anthropic/claude-sonnet-4"]
input_per_million = 3.0
output_per_million = 15.0

[model_profiles.fast]
model = "google/gemini-2.5-flash-preview"
max_tool_rounds = 15

[model_profiles.deep]
model = "minimax/minimax-m2.7"
max_tool_rounds = 40
reasoning_effort = "high"

[model_routing]
enabled = true
rules = [
  { match = "fix test|pytest|failing test", profile = "deep" },
  { match = "summarize|explain", profile = "fast" },
]
```

Task routing applies when you omit `--model-profile` (REPL/TUI/run print `Auto-routed model profile: …`).

## Usage

```powershell
agent run "Find why tests fail and fix them" --cwd examples/demo-project
agent run "fix failing tests" --model-profile deep
agent review --uncommitted --cwd examples/demo-project
agent run "design refactor" --plan
agent run "extract deps" --output-schema schema.json --json
agent repl --cwd examples/demo-project
agent tui --resume-last
agent hooks list
agent memories list
agent memories suggest
agent runs export <turn-id> --format bundle --out run.bundle.tar.gz
agent doctor --json
```

See [docs/codex-comparison.md](docs/codex-comparison.md) for Codex parity details.

### Key flags

| Flag | Description |
|------|-------------|
| `--auto-approve` | Skip all approval prompts |
| `--session-auto-approve` | Session-wide auto-approve |
| `--model-profile` | Named profile from config |
| `--sandbox` | `danger-full-access`, `read-only`, or `workspace-write` |
| `--title` | Thread title on new thread |
| `--resume-last` | Resume latest thread for cwd |
| `--jsonl-events` | Legacy machine-readable event stream |
| `--json` | Normalized Codex-like JSONL events + run summary |
| `--plan` | Plan mode (read-only tool subset) |
| `--output-schema` | Validate final assistant JSON against schema |
| `--quiet-tools` | Hide tool lines on stderr |
| `--skip-git-check` | Allow run outside git (CI/fixtures) |

### Approval keys

- `y` — approve once
- `n` — deny (default)
- `a` — approve all for this **turn**
- `A` — approve all for this **session**
- `p` — approve and save command prefix to project allow list (first two tokens)

## Sandbox & exec policy

Heuristic checks before shell/MCP/file tools — **not OS-level isolation** (kernel/AppContainer opt-in in [enterprise.md](docs/enterprise.md)). The default approval mode is `interactive`; `--auto-approve` or `approval_mode = "auto"` are explicit opt-ins. The CLI warns when local execution is unisolated or when `danger-full-access` is combined with auto approval.

| Mode | Behavior |
|------|----------|
| `danger-full-access` | No sandbox restrictions; explicit high-risk mode |
| `read-only` | Blocks writes and network-like shell |
| `workspace-write` | Writes only under thread `cwd` |

```powershell
agent exec-policy test "git status --short"
agent exec-policy test "del /s /q foo"
agent exec-policy amend --prefix "pytest -q"
```

```toml
[web_search]
enabled = true
provider = "exa"   # duckduckgo | exa | tavily
# api_key_env = "EXA_API_KEY"
```

```powershell
agent mcp-server   # stdio MCP server — expose agent_run to Claude Desktop / Cursor
agent review "security focus" --uncommitted --json
```

`exec_policy`: `prompt` | `untrusted` (allow-list + read/test auto) | `never` (CI + `--auto-approve`).

For command isolation, prefer Docker or SSH:

```powershell
agent run "run tests" --execution-backend docker
```

Docker uses hardened flags by default (`--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--user 65532:65532`) while keeping the workspace mount writable or read-only according to the sandbox mode.

## Serve hardening

`agent serve` binds to `127.0.0.1` by default and refuses non-local binds unless `--allow-remote-bind` / `serve.allow_remote_bind = true` is set. Query-string tokens (`?token=` / `?session=`) are disabled by default; use bearer/session headers instead, or explicitly set `serve.allow_query_tokens = true` for legacy clients.

```toml
[serve]
host = "127.0.0.1"
auth_mode = "bearer"
allow_remote_bind = false
allow_query_tokens = false
max_request_body_bytes = 1048576
redact_thread_responses = true
cors = true
cors_allowed_origins = ["https://dashboard.example"]
```

When CORS is enabled, the server echoes only configured origins; it does not emit wildcard CORS for session/OIDC flows. OIDC session cookies include `Secure` when TLS is enabled. Thread JSON responses redact command output, file contents, diffs, and large message bodies by default; an admin can request full output with `?full=1`.

Opt-in persistent shell (`[shell] enabled = true`) supports `stdin`, output caps (`max_output_chars`), and partial returns (`yield_ms`). Read-only tools dispatch in parallel within a model round when configured via `[harness] max_parallel_read_tools`.

```powershell
agent threads pick              # interactive resume picker
agent run "task" --resume       # pick thread, then run
agent run "task" --ephemeral    # no thread JSONL saved
agent apply --dry-run           # preview last agent patch
```

```toml
[notify]
command = "powershell -Command Write-Host Done: $env:AGENT_STATUS"
```

## Threads & cost

```powershell
agent threads list
agent threads show <id> --usage
agent threads fork <id> --title "experiment"
agent threads rename <id> "fix calc bug"
agent threads cost <id>
agent threads pr-description <id>    # PR body (use --summary-only for bullets)
agent threads resume-turn <id>       # Resume cancelled turn from checkpoint
agent threads checkpoint-status <id>
```

Fork lineage stored as `forked_from` in JSONL metadata.

Thread storage remains JSONL-compatible. New writes also maintain `<thread_id>.meta.json` sidecar metadata, use lock files around append/rewrite operations, and use atomic replacement for rewrites so thread lists and prefix lookup can stay metadata-only without loading full transcripts.

## Run recording

```powershell
agent runs list --thread-id <id>
agent runs show <turn-prefix> --human
agent runs replay <turn-prefix>
```

Logs: `~/.agent-cli/runs/{thread_id}/{turn_id}.jsonl`.

## Skills, MCP, tools

```powershell
agent skills list --cwd examples/demo-project
agent skills doctor --cwd examples/demo-project
agent mcp list
agent tools list --cwd examples/demo-project
```

Built-in: `read_file`, `search_repo`, `apply_patch`, `write_file`, `run_command`, optional `web_search`, `git_commit` (git repos only), MCP tools.

**Code navigation:** Built-in `file_outline`, `go_to_definition`, `find_references`, `file_imports`. **LSP (Pyright + TS):** enable `[mcp_servers.lsp]` and use `mcp__lsp__*` tools — see [docs/code-navigation.md](docs/code-navigation.md).

```powershell
pip install pyright
agent lsp-mcp --workspace .   # standalone MCP server
```

Project rules: `AGENTS.md` / `agents.md` / `.agents/AGENTS.md` injected into the system prompt. **Project context** (README, manifests, CI snippet, repo map) is auto-injected separately.

## Interactive TUI

Running **`agent`** with no subcommand opens the chat UI in the current directory. Project config (`.agent-cli/config.toml`) is created automatically on first launch — no `agent init` required.

```powershell
pip install -e ".[dev]"
agent --cwd examples/demo-project
```

Or explicitly:

```powershell
agent tui --cwd examples/demo-project
agent repl --cwd examples/demo-project
```

| Key / input | Action |
|-------------|--------|
| Enter | Submit turn |
| Shift+Enter | New line in composer |
| `@file` / `@skill` | Mention popup (Tab or ↑/↓ to pick); draft restored on resume |
| Ctrl+R | Reverse-search composer history |
| Ctrl+T | Full transcript overlay (composer locked while open) |
| `e` | Expand/collapse long tool output or diffs |
| `/help` | Slash commands (`/model`, `/plan`, `/compact`, `/cost`, `/quit`, …) |
| `y`/`n`/`a`/`A` | Approvals (modal overlay or inline banner; composer locked in modal) |
| Ctrl+C | Cancel turn |
| `q` | Quit (home screen) |

**Transcript:** typed cells (tools, patches, plan, compaction, MCP, web search) with Codex-style patch diffs — truecolor/256/16 add-delete backgrounds via `diff_palette.py`, hunk syntax highlight, truncation headers on large exec output. **Composer draft** (text + `@` bindings) persists per thread under `.agent-cli/composer-drafts/`.

Footer shows model, plan mode, and context usage (`Context N% left · M% used`). Details: [docs/ui-plan.md](docs/ui-plan.md), colors: [docs/tui-styles.md](docs/tui-styles.md).

**Debugging:** every turn writes a plain-text trace to `.agent-cli/action.log` (project) and `~/.agent-cli/logs/<thread_id>.log`. Look for `tool pending` → `tool executing` → `tool done` per tool. See [docs/action-log.md](docs/action-log.md).

## Models & routing

```powershell
agent models list
agent models recommend --task "fix failing pytest tests"
agent profile list
agent profile show interactive
```

OpenRouter fallbacks, retries, and per-turn cost estimates are on by default. Native OpenRouter routing (`models` + `route: "fallback"`) is enabled when multiple models are configured; set `native_fallback = false` for client-side sequential fallback. Cost prefers `usage.cost` from the API when available. See [docs/openrouter.md](docs/openrouter.md) for reasoning preservation, structured output, and all `[openrouter]` keys.

`agent doctor` includes a solo-dev readiness table, OpenRouter reachability when a key is set, and **`agent doctor --models`** for per-profile preflight (tools, context, pricing).

```powershell
agent run "fix tests" --max-cost 0.50          # stop turn when estimated cost exceeds cap
agent review --base main --json --fail-on critical,major   # CI exit code 1 on findings
# config: [harness] post_patch_test = "pytest -q"
agent threads show <thread-id> --stats
```

See [docs/json-events.md](docs/json-events.md) for the `--json` event catalog.

## Demo project golden path

`examples/demo-project` ships a deliberate `calc.py` bug, pytest suite, skill, and config:

```powershell
cd examples/demo-project
.\setup.ps1
agent init --yes
agent run "fix failing tests"
python -m pytest -q
```

## Tests

```powershell
pytest              # 1200+ tests
pytest -m lsp       # optional; needs pyright + typescript-language-server on PATH
```

## Release notes

See [CHANGELOG.md](CHANGELOG.md) for v2.11.0 (LSP MCP), v2.10.0 (code navigation + project context), and earlier releases.

## Further reading

- [Documentation index](docs/README.md) — all guides, release timeline, config patterns
- [Code navigation](docs/code-navigation.md) — LSP MCP, built-in outline/defs/refs, project context
- [Codex comparison](docs/codex-comparison.md) — parity matrix
- [TUI UX plan](docs/ui-plan.md) — Codex-style transcript roadmap
- [TUI style guide](docs/tui-styles.md) — diff colors, approval chrome, composer behavior
- [Action log](docs/action-log.md) — human-readable turn trace and stuck-turn diagnosis
- [OpenRouter integration](docs/openrouter.md) — fallbacks, reasoning, structured output, cost
- [Session context](docs/session-context.md) — Cursor/agent briefing (phase, rules, next work)
- [Roadmap (Phases 24–28)](docs/roadmap/README.md) — step-by-step harness plans
- [Enterprise features](docs/enterprise.md) — serve, OIDC, RBAC, DAG, scheduler (Phases 6–18)
