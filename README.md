# agent-cli

A local coding agent CLI inspired by OpenAI Codex — powered by [OpenRouter](https://openrouter.ai).

## Install

```powershell
cd agent-cli
pip install -e ".[dev]"
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

`read_file`, `search_repo`, `apply_patch`, `write_file`, `run_command` + MCP tools.

## Project rules

Loads `AGENTS.md`, `agents.md`, or `.agents/AGENTS.md` into `# Project Rules` in the system prompt.

## Tests

```powershell
pytest   # 111+ tests
```

## Phase 4 migration

New optional config keys (`sandbox_mode`, `exec_policy`, `[compaction]`, `[openrouter]`). Defaults preserve prior behavior (`danger-full-access`, `exec_policy=prompt`). Thread JSONL adds optional `forked_from` and `title` fields — older threads load unchanged.

New JSONL event types: `sandbox.blocked`, `compaction.completed` (plus legacy `compaction`).

## Phase 5 (not yet)

Kernel sandbox (Seatbelt/bubblewrap/Windows restricted token), web UI/TUI, web search, multi-agent orchestration, cloud execution, skill marketplace.
