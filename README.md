# agent-cli

A local coding agent CLI inspired by OpenAI Codex. Give it a task, and it investigates your repo with tools, edits files, runs commands, and streams progress — powered by [OpenRouter](https://openrouter.ai).

## Requirements

- Python 3.11+
- An [OpenRouter API key](https://openrouter.ai/keys)
- Optional: [git](https://git-scm.com/) for repo-root detection
- Optional: [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for faster repo search

## Install

```powershell
cd agent-cli
pip install -e ".[dev]"
```

## Configure

### Environment variables

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
# optional overrides:
$env:OPENROUTER_MODEL = "anthropic/claude-sonnet-4"
$env:OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
$env:AGENT_APPROVAL_MODE = "interactive"   # or "auto"
$env:AGENT_MAX_TOOL_ROUNDS = "25"
$env:AGENT_CONTEXT_WINDOW_TOKENS = "128000"
$env:AGENT_COMPACTION_THRESHOLD = "0.7"
```

### Config file (`~/.agent-cli/config.toml`)

```toml
model = "anthropic/claude-sonnet-4"
approval_mode = "interactive"   # interactive | auto
max_tool_rounds = 25
command_timeout_sec = 120
max_tool_output_chars = 20000
default_cwd = "e:/lampcode"     # optional
context_window_tokens = 128000
compaction_threshold = 0.7

[tools]
prefer_ripgrep = true
```

**Precedence:** CLI flags → environment variables → config file → built-in defaults.

```powershell
agent config show
agent doctor
```

## Usage

```powershell
agent run "Find why tests fail and fix them" --cwd e:\lampcode\agent-cli\examples\demo-project
```

### Flags

| Flag | Description |
|------|-------------|
| `--cwd PATH` | Project directory |
| `--thread-id ID` | Resume a thread by ID |
| `--resume-last` | Resume most recent thread for same cwd |
| `--model MODEL` | OpenRouter model slug |
| `--auto-approve` | Skip approval prompts |
| `--max-rounds N` | Max tool-call rounds (default: 25) |
| `--skip-git-check` | Suppress non-git-repo warning |
| `--jsonl-events` | Emit machine-readable JSONL events on stdout |
| `--quiet-tools` | Hide tool lines; show approvals + final answer |

### Examples

```powershell
# Interactive mode (prompts before commands/writes/patches)
agent run "Add type hints to utils.py" --cwd E:\myproject

# Trusted mode
agent run "Run tests and fix failures" --cwd E:\myproject --auto-approve

# Resume
agent run "Continue fixing tests" --resume-last --cwd E:\myproject

# Machine-readable event stream
agent run "diagnose bug" --cwd E:\myproject --jsonl-events

# Thread management
agent threads list
agent threads show abc12345
agent threads delete abc12345 --yes
```

## Tools

| Tool | Approval | Description |
|------|----------|-------------|
| `read_file` | No | Read files in the project |
| `search_repo` | No | Regex search (`rg` or Python fallback) |
| `apply_patch` | Yes | Structured patch edits (preferred for modifications) |
| `write_file` | Yes | Full file overwrite (new files / full rewrites) |
| `run_command` | Yes | Shell commands (classified as read/test/run/write) |

### Patch format (`apply_patch`)

```text
*** Begin Patch
*** Update File: calc.py
@@
-    return a - b
+    return a + b
*** Add File: new.py
+line 1
*** Delete File: old.py
*** End Patch
```

## How it works

1. Resolves config (CLI → env → `~/.agent-cli/config.toml`).
2. Detects git repo root (warns if missing, unless `--skip-git-check`).
3. Streams assistant text and calls tools with approval gates.
4. Compacts older history when context exceeds 70% of configured window.
5. Persists to `~/.agent-cli/threads/{thread_id}.jsonl`.
6. Ctrl+C cancels gracefully — partial progress is saved.

### Approval keys

- `y` — approve this action
- `n` — deny (default)
- `a` — approve all for the current turn

## Project layout

```
agent-cli/
├── agent/          models, loop, persistence, context, compaction, events
├── tools/          file, shell, search, patch tools + registry
├── model/          OpenRouter streaming client
├── approval/       interactive approval gate
└── cli/            typer CLI entrypoint
```

## Tests

```powershell
pytest
```

## Troubleshooting

**Tool calling errors:** Try `--model anthropic/claude-sonnet-4`.

**Force compaction testing:** Lower threshold in config:

```toml
context_window_tokens = 2000
compaction_threshold = 0.5
```

**Migration:** Existing thread JSONL files load fine; new optional fields (`repo_root`, `change_type`, etc.) default safely.

## Phase 3 (not yet implemented)

- MCP client/server
- Docker/OS sandbox
- Web UI / TUI
- Multi-agent orchestration
- Web search
