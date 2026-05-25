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
| `--resume-last` | Resume latest thread for cwd |
| `--jsonl-events` | Machine-readable event stream |
| `--show-system-prompt` | Debug: print system prompt |
| `--quiet-tools` | Hide tool lines |

### Approval keys

- `y` — approve once
- `n` — deny (default)
- `a` — approve all for this **turn**
- `A` — approve all for this **session**

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
pytest
```

## Phase 4 (not yet)

OS sandbox, web UI/TUI, web search, multi-agent, skill marketplace.
