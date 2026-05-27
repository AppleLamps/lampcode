# Context management

agent-cli meters context with a hybrid of **local estimates** (tiktoken when installed, else char÷4) and **API usage** from the last OpenRouter completion (`prompt_tokens` / `total_tokens`).

## Settings

```toml
[context]
baseline_mode = "auto"      # auto | fixed | none
baseline_tokens = 12000     # when baseline_mode = fixed
headroom_tokens = 8000      # reserved for the next tool round
headroom_pct = 0.05
warn_left_pct = 25          # yellow traffic light on status line
artifact_inline_limit = 8000

[compaction]
model = "google/gemini-2.5-flash-preview"
pre_turn_threshold = 0.85
tool_output_threshold = 0.75
```

**Effective window** = model window − baseline − headroom. Percent-left uses `max(estimate, api)` against the effective window.

Optional accurate counting:

```bash
pip install agent-cli[tokenizer]
```

## Commands

- TUI: `/context` or `/context --json`
- CLI: `agent context [--thread ID] [--json]`

For a plain-text trace of each turn (tools, compaction, errors), see [action-log.md](action-log.md).

## Artifacts

Large tool outputs spill to `.agent-cli/artifacts/<thread_id>/<item_id>.txt`. Thread items keep a short inline summary plus the artifact path.

## Compaction tiers

1. **Tool-output compact** — older turns get shortened command/MCP outputs when estimate exceeds `tool_output_threshold × effective_window`.
2. **Full compact** — LLM summary checkpoint (uses `[compaction].model` when set).
3. **Pre-turn guard** — before each model call, if over `pre_turn_threshold`, tier 1 then tier 2 as needed.
4. **Context-length retry** — one automatic full compact + retry on classified context-length errors.
