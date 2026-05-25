# agent-cli

A local coding agent CLI inspired by OpenAI Codex — powered by [OpenRouter](https://openrouter.ai).

## Install

```powershell
cd agent-cli
pip install -e ".[dev]"
pip install -e ".[tui]"    # optional: interactive terminal UI
```

## Configure

Environment + `~/.agent-cli/config.toml` (CLI flags → env → file → defaults).

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
agent config show
agent doctor
agent doctor --deep   # optional MCP startup check
```

### Example `~/.agent-cli/config.toml`

```toml
model = "anthropic/claude-sonnet-4"
approval_mode = "interactive"
max_tool_rounds = 25
sandbox_mode = "workspace-write"   # danger-full-access | read-only | workspace-write
exec_policy = "untrusted"          # prompt | untrusted | never

[exec_policy_rules]
allow = [
  "git status*",
  "git diff*",
  "pytest*",
  "python -m pytest*",
  "rg *",
  "dir",
  "dir *",
]
deny = [
  "rm -rf *",
  "del /s *",
  "format *",
]

[compaction]
enabled = true
keep_recent_turns = 2
threshold = 0.7
summary_max_chars = 8000

[openrouter]
max_retries = 3
retry_base_delay_sec = 1.0
request_timeout_sec = 120

[recording]
enabled = true
keep_last_runs_per_thread = 50

[isolation]
enabled = true
strip_env = true
kill_process_tree_on_timeout = true
clear_network_env_hints = true

[web_search]
enabled = false
provider = "duckduckgo"
max_results = 5
timeout_sec = 15

[skills]
max_active = 3
max_body_chars = 4000
enable_user_skills = true
enable_project_skills = true
project_rules_max_chars = 8000

[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "E:/lampcode/agent-cli/examples/demo-project"]
enabled = true
require_approval = false

[mcp]
startup_timeout_sec = 30
tool_name_prefix = true
tool_call_timeout_sec = 60
```

Project-local MCP override: `{cwd}/.agent-cli/mcp.toml` (wins on name collision).

## Usage

```powershell
agent run "Find why tests fail and fix them" --cwd e:\lampcode\agent-cli\examples\demo-project
```

### Key flags

| Flag | Description |
|------|-------------|
| `--auto-approve` | Skip all approval prompts |
| `--session-auto-approve` | Session-wide auto-approve (thread run) |
| `--sandbox` | `danger-full-access` (default), `read-only`, or `workspace-write` |
| `--title` | Set thread title when creating a new thread |
| `--resume-last` | Resume latest thread for cwd |
| `--jsonl-events` | Machine-readable event stream |
| `--show-system-prompt` | Debug: print system prompt |
| `--quiet-tools` | Hide tool lines |
| `--execution-backend` | `local` (default) or `docker` for `run_command` |
| `--docker-image` | Override Docker image when using docker backend |
| `--multi-agent` | Enable `spawn_worker` supervisor tool |

### Approval keys

- `y` — approve once
- `n` — deny (default)
- `a` — approve all for this **turn**
- `A` — approve all for this **session**

## Sandbox modes (Phase 4)

Heuristic policy checks before shell/MCP/file tools run — **not OS-level isolation**.

| Mode | Behavior |
|------|----------|
| `danger-full-access` | Default; same as pre-Phase 4 |
| `read-only` | Blocks write-like and network-like shell commands, file writes, patches, mutating MCP tools |
| `workspace-write` | Writes allowed only under thread `cwd`; blocks network-like commands |

```powershell
# Block file creation in read-only mode
agent run "create file foo.txt" --cwd e:\lampcode\agent-cli\examples\demo-project --sandbox read-only

# Allow writes under cwd only
agent run "fix tests" --cwd e:\lampcode\agent-cli\examples\demo-project --sandbox workspace-write
```

CLI `--sandbox` overrides `sandbox_mode` in config. Denied commands emit `sandbox.blocked` events and persist as `commandExecution` with `status=denied`.

**Limitations:** redirect/filename parsing is best-effort; sub-shells, scripts, and indirect writes are not fully contained.

## Exec policy

Auto-approve safe read-only commands when `exec_policy = "untrusted"`:

```powershell
agent exec-policy test "git status --short"
agent exec-policy test "del /s /q foo"
```

- `prompt` — always prompt (plus turn/session approvals)
- `untrusted` — auto-approve allow-list + read/test commands; prompt for the rest
- `never` — never prompt (intended for CI with `--auto-approve`; `agent doctor` warns)

## Thread fork & titles

```powershell
agent threads list
agent threads fork abc123 --title "experiment"
agent run "try alternate fix" --thread-id <forked-id>
agent threads rename abc123 "fix calc bug"
agent run "fix it" --title "calc bug"   # title on new thread only
```

Forked threads store `forked_from` in JSONL metadata; `agent threads list` shows lineage.

## Interactive TUI (Phase 5)

```powershell
pip install -e ".[tui]"
agent tui --cwd e:\lampcode\agent-cli\examples\demo-project
agent tui --resume-last
```

Layout: thread list (left), live transcript (right), status bar, input at bottom.

| Key | Action |
|-----|--------|
| Enter | Submit turn (or approval key when pending) |
| `y` / `n` / `a` / `A` | Approve / deny / turn / session (inline during approvals) |
| Ctrl+C | Cancel running turn |
| Ctrl+L | Clear input |
| `q` | Quit (warns if turn running) |

Headless `agent run` is unchanged — TUI is optional.

## Run recording & replay

Every turn writes `~/.agent-cli/runs/{thread_id}/{turn_id}.jsonl` (one `AgentEvent` per line).

```powershell
agent runs list
agent runs list --thread-id abc123
agent runs show turn-id-prefix --human
agent runs replay turn-id-prefix
```

Use `--jsonl-events` on `agent run` for stdout streaming; run logs mirror the same event schema.

## Subprocess isolation (Phase 5)

When `sandbox_mode` is not `danger-full-access` and `[isolation] enabled = true`:

- Subprocess cwd locked to thread cwd
- Environment stripped to allowlist (+ proxy vars removed)
- Timeout kills process tree (`taskkill /T /F` on Windows)

Emits `isolation.applied` events. **Not** true network/filesystem isolation — env hardening only.

## Web search (optional, off by default)

```toml
[web_search]
enabled = true
```

Requires approval in interactive mode; blocked in `read-only` sandbox. Uses DuckDuckGo (no API key). Results persist as `webSearch` items.

```powershell
# Enable in config, then ask the agent to search
agent run "look up pytest exit code 1 docs" --cwd examples/demo-project
```

## Skills

Skills live in folders with `SKILL.md`:

```
{cwd}/.agent-cli/skills/my-skill/SKILL.md
{cwd}/skills/my-skill/SKILL.md
~/.agent-cli/skills/my-skill/SKILL.md
```

```powershell
agent skills list --cwd e:\lampcode\agent-cli\examples\demo-project
agent skills show pytest-fix --cwd e:\lampcode\agent-cli\examples\demo-project
```

Skills are auto-selected by keyword overlap or `@skill-name` mentions (max 3 per turn).

## MCP

```powershell
agent mcp list
agent mcp tools --cwd e:\lampcode\agent-cli\examples\demo-project
```

MCP tools are exposed as `mcp__{server}__{tool}` and persisted as `mcpToolCall` items.

## Built-in tools

`read_file`, `search_repo`, `apply_patch`, `write_file`, `run_command`, optional `web_search` + MCP tools.

## Project rules

Loads `AGENTS.md`, `agents.md`, or `.agents/AGENTS.md` into `# Project Rules` in the system prompt.

## Phase 6 — Docker execution + multi-agent supervisor

### Execution backends

Commands from `run_command` can run on the host (`local`, default) or inside Docker (`docker`).

```toml
[execution]
backend = "local"          # local | docker
default_image = "python:3.12-slim"
workspace_mount = "/workspace"
network = "none"           # none | bridge (bridge needs approval first use)
memory_limit = "1g"
cpu_limit = "1.0"
command_timeout_sec = 120
auto_pull = false

[execution.docker]
binary = "docker"
platform = ""
```

Precedence: `--execution-backend` → `AGENT_EXECUTION_BACKEND` → config → `local`.

```powershell
# Host (default)
agent run "pytest -q" --cwd e:\lampcode\agent-cli\examples\demo-project

# Docker (requires Docker Desktop on Windows)
agent run "python -c `"print(1+1)`"" `
  --execution-backend docker `
  --docker-image python:3.12-slim `
  --cwd e:\lampcode\agent-cli\examples\demo-project

agent doctor --deep   # checks docker binary + hello-world (skip in CI via AGENT_SKIP_DOCKER_INTEGRATION=1)
```

Docker mounts thread `cwd` at `/workspace` (read-only when sandbox is `read-only` and command is read-like). Default network is **none** for safety. `commandExecution` items record `backend`, `container_id`, and `image` when applicable.

### Multi-agent supervisor (v1)

When enabled, the main agent gets a `spawn_worker` tool — fork a worker thread, run **one** headless turn, return the summary (max **3** spawns per supervisor turn; workers cannot spawn).

```toml
[multi_agent]
enabled = true
max_workers_per_turn = 3
worker_auto_approve = false
inherit_execution_backend = true
```

```powershell
agent run "Refactor tests and docs in parallel" `
  --multi-agent `
  --cwd e:\lampcode\agent-cli\examples\demo-project
```

### Export & read-only viewer

```powershell
agent threads export abc123 --out thread.md
agent runs export turn-id-prefix --out run.md

agent serve --host 127.0.0.1 --port 8765
# GET http://127.0.0.1:8765/threads
# GET http://127.0.0.1:8765/threads/{id}
# GET http://127.0.0.1:8765/runs/{turn_id}
```

Binding to `0.0.0.0` prints a warning — localhost only by default.

## Tests

```powershell
pytest   # 165+ tests
```

## Phase 5 migration

New optional sections: `[recording]`, `[isolation]`, `[web_search]`. Defaults preserve prior behavior (recording on, isolation auto when sandbox restricted, web search off). New item type `webSearch`, events `isolation.applied`. Install `[tui]` extra for `agent tui`.

## Phase 7 (planned, not implemented)

Kernel sandbox (AppContainer/bubblewrap/Seatbelt), SSH remote execution backend, recursive worker swarms, skill marketplace, full editable web UI.
