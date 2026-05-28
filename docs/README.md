# agent-cli documentation

**Current release:** [v2.11.0](../CHANGELOG.md) — LSP MCP (Pyright + TypeScript) on top of v2.10 harness intelligence and v2.9.x TUI polish.

Solo developers start at the [README](../README.md). Paste [session-context.md](session-context.md) into Cursor when continuing harness work.

## By topic

| Doc | What it covers |
|-----|----------------|
| [codex-comparison.md](codex-comparison.md) | Living parity matrix vs official Codex CLI (harness + TUI tiers) |
| [code-navigation.md](code-navigation.md) | LSP MCP tools, built-in `code_intel`, project context, doctor |
| [context.md](context.md) | Token metering, compaction, AGENTS.md / skills / memories inject |
| [openrouter.md](openrouter.md) | Models, fallbacks, reasoning, structured output, cost |
| [json-events.md](json-events.md) | `agent run --json` event catalog |
| [action-log.md](action-log.md) | Human-readable turn trace (stuck tools, approvals) |
| [ui-plan.md](ui-plan.md) | TUI transcript architecture, Codex gaps, backlog |
| [tui-styles.md](tui-styles.md) | Semantic colors, diff palette, composer behavior |
| [run-bundle.md](run-bundle.md) | Debug replay bundle export/import |
| [enterprise.md](enterprise.md) | Opt-in serve, OIDC, RBAC, DAG, scheduler (Phases 6–18) |
| [roadmap/README.md](roadmap/README.md) | Historical Phases 24–28 implementation plans |

## Release timeline (post-roadmap)

| Version | Theme |
|---------|--------|
| **2.11.0** | `agent lsp-mcp`, `mcp__lsp__*`, shared `agent/lsp/`, `ide_lsp` completions |
| **2.10.0** | Built-in code navigation, `project_context.py`, prompt DSL, TUI incremental sync + controllers |
| **2.9.4** | Diff palette, composer drafts, modal composer lock |
| **2.9.2** | Default `agent` → TUI, Grok home, slash commands, context footer |
| **2.9.1** | Native OpenRouter fallback, reasoning preservation, structured output |
| **2.9.0** | Phase 28 — bundles, hooks v2, memories v3, plan mode v2, doctor JSON |

## Quick config patterns

**Config precedence:** CLI flags → env → `~/.agent-cli/config.toml` → `{cwd}/.agent-cli/config.toml` → defaults.

**LSP once (all projects)** — in `~/.agent-cli/config.toml`:

```toml
[mcp_servers.lsp]
command = "agent"
args = ["lsp-mcp"]
enabled = true
require_approval = false
```

Install: `pip install pyright` and `npm i -g typescript typescript-language-server`.

**Plan mode** keeps LSP read tools by default:

```toml
[plan_mode]
allow_mcp_servers = ["lsp"]
```

**Harness hooks:**

```toml
[harness]
post_patch_test = "pytest -q"              # auto-detected on init when possible
lsp_diagnostics_after_patch = false        # optional; needs LSP MCP connected
max_parallel_read_tools = 4                # batch read_file / search / code-nav / LSP reads
```

## Verify before claiming done

```powershell
cd e:\lampcode\agent-cli
pytest -q
git log --oneline -3
```

Expect **1200+** pytest cases at v2.11.0. Optional LSP integration: `pytest -m lsp` (requires language servers on PATH).
