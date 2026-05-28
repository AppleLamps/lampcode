# Code navigation and project context

How agent-cli helps the model explore a repo: **LSP via MCP** when configured, plus built-in fallbacks.

## LSP MCP (recommended for Python / TypeScript)

Install language servers:

```powershell
pip install pyright
npm i -g typescript typescript-language-server
```

Enable in **project** config (`agent init` includes a commented block) or **once globally** in `~/.agent-cli/config.toml` (applies to every repo):

```toml
[mcp_servers.lsp]
command = "agent"
args = ["lsp-mcp"]
enabled = true
require_approval = false

[plan_mode]
allow_mcp_servers = ["lsp"]   # default; keeps LSP tools in --plan mode
```

Config precedence: CLI → env → `~/.agent-cli/config.toml` → `{cwd}/.agent-cli/config.toml` → defaults.

Run standalone for debugging:

```powershell
agent lsp-mcp --workspace .
```

### MCP tools (exposed as `mcp__lsp__*`)

| Tool | LSP | Purpose |
|------|-----|---------|
| `lsp_definition` | `textDocument/definition` | Go to definition |
| `lsp_references` | `textDocument/references` | Find references |
| `lsp_document_symbols` | `textDocument/documentSymbol` | File outline |
| `lsp_hover` | `textDocument/hover` | Type / signature at cursor |
| `lsp_workspace_symbol` | `workspace/symbol` | Search symbols workspace-wide |
| `lsp_diagnostics` | pull / publish diagnostics | Errors and warnings for a file |
| `lsp_rename` | `textDocument/rename` | Multi-file rename plan (apply via `apply_patch`) |

**Arguments:** `path` (repo-relative), `line` (1-based), `character` (0-based column on that line). Workspace symbol uses `query`.

**Plan mode:** `[plan_mode] allow_mcp_servers = ["lsp"]` (default) keeps LSP tools available with read-only builtins.

**Optional:** `[harness] lsp_diagnostics_after_patch = true` appends diagnostics after successful `apply_patch` when the LSP MCP server is connected.

Implementation: `agent/lsp/`, `agent/lsp_mcp/`.

## Built-in navigation tools (fallback)

| Tool | Purpose |
|------|---------|
| `file_outline` | Symbols in one file (Python AST; regex for JS/TS/Go/Rust) |
| `go_to_definition` | Definition search (AST + repo patterns) |
| `find_references` | Word-boundary ripgrep |
| `file_imports` | Imports with resolved local paths |

Use when LSP is not configured, times out, or for unsupported languages.

Implementation: `tools/code_intel.py`.

## System prompt

- **LSP precedence** — when `mcp__lsp__*` tools are connected, the prompt tells the model to prefer them for Python/TS.
- **`apply_patch` DSL**, **plan mode** `<proposed_plan>`, **Windows** `search_repo` — see `agent/context.py`.

## Project context (auto-injected)

`load_project_context()` in `agent/project_context.py`: README, manifests, CI snippet, test layout, repo map, skill names (~12 KB).

## Doctor

`agent doctor` reports Pyright / typescript-language-server on PATH and whether `[mcp_servers.lsp]` is configured.

## IDE completions (`agent serve`)

When enterprise **serve** is running with IDE enabled, `GET /ide/completions` uses the same **`agent/lsp/`** client as the MCP server (not the lightweight subprocess diagnostics in `[serve.ide.diagnostics]`). See [enterprise.md](enterprise.md) Phase 14 + IDE v3.

## Related

- [docs/README.md](README.md) — documentation index
- [codex-comparison.md](codex-comparison.md)
- [context.md](context.md)
