# agent-cli

A local coding agent CLI inspired by OpenAI Codex. Give it a task, and it investigates your repo with tools, edits files, runs commands, and streams progress — powered by [OpenRouter](https://openrouter.ai).

## Requirements

- Python 3.11+
- An [OpenRouter API key](https://openrouter.ai/keys)
- Optional: [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) for faster repo search

## Install

```bash
cd agent-cli
pip install -e ".[dev]"
```

## Configure

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
# optional overrides:
export OPENROUTER_MODEL=anthropic/claude-sonnet-4
export OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

## Usage

Run the agent on a task in any git repo:

```bash
agent run "Find why tests fail and fix them" --cwd /path/to/your/repo
```

### Flags

| Flag | Description |
|------|-------------|
| `--cwd PATH` | Project directory (defaults to current directory) |
| `--thread-id ID` | Resume a previous conversation |
| `--model MODEL` | OpenRouter model slug |
| `--auto-approve` | Skip approval prompts for shell commands and file writes |
| `--max-rounds N` | Max tool-call rounds per turn (default: 25) |

### Examples

```bash
# Interactive mode (prompts before commands/writes)
agent run "Add type hints to utils.py" --cwd ~/myproject

# CI / trusted mode
agent run "Run tests and fix failures" --cwd ~/myproject --auto-approve

# Resume a thread
agent run "Continue fixing the remaining tests" --thread-id abc12345-...

# List threads
agent threads list

# Show transcript
agent threads show abc12345
```

## How it works

1. You send a task via `agent run`.
2. The agent streams assistant text and calls tools:
   - `read_file` — read files in the project
   - `write_file` — create/overwrite files (requires approval)
   - `run_command` — run shell commands (requires approval)
   - `search_repo` — grep the repo (uses `rg` if available, else Python)
3. Shell commands and file writes ask for `[y/N]` approval unless `--auto-approve`.
4. Conversation history is saved to `~/.agent-cli/threads/{thread_id}.jsonl`.
5. The agent stops when the model returns a final message with no tool calls.

## Project layout

```
agent-cli/
├── agent/          # models, loop, persistence, context
├── tools/          # file, shell, search tools + registry
├── model/          # OpenRouter streaming client
├── approval/       # interactive approval gate
└── cli/            # typer CLI entrypoint
```

## Tests

```bash
pytest
```

## Troubleshooting

**Tool calling errors:** Some models don't support function calling. Try:

```bash
agent run "..." --model anthropic/claude-sonnet-4
```

**Missing API key:**

```bash
export OPENROUTER_API_KEY=your_key
```

**Search fallback:** If `rg` is not installed, search uses a pure-Python walker automatically.

## Phase 2 (not yet implemented)

- Context compaction for long threads
- Additional sandboxing
