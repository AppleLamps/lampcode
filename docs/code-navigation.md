# Code navigation and project context

How agent-cli helps the model explore a repo without a full IDE LSP.

## Built-in navigation tools

| Tool | Purpose |
|------|---------|
| `file_outline` | List functions, classes, and top-level symbols in one file (Python AST; regex for JS/TS/Go/Rust) |
| `go_to_definition` | Find definition sites; optional `path_hint` searches that file first |
| `find_references` | Word-boundary search for a symbol across the repo (prefer over raw regex for identifiers) |
| `file_imports` | List imports/requires and resolve project-local paths when possible |

All are **read-only**, batched in parallel with `read_file` / `search_repo` / `web_search` when `[harness] max_parallel_read_tools` allows.

Implementation: `tools/code_intel.py`, registered in `tools/registry.py`.

### Limits

This is **not** a language server:

- No cross-file type inference or rename-all
- JS/Go/Rust outlines use regex heuristics, not full parsers
- Import resolution only handles common Python and relative JS/TS paths

For IDE-grade navigation, add an **LSP MCP server** and expose it alongside built-in tools.

## System prompt

The agent system prompt includes:

- **`apply_patch` DSL** — `*** Begin Patch` format with examples (`tools/patch.py` → `APPLY_PATCH_FORMAT_DOCS`)
- **Plan mode** — `<proposed_plan>...</proposed_plan>` when `agent run --plan` (`agent/plan_mode.py`)
- **Code navigation** — when to use the tools above (`CODE_NAVIGATION_DOCS` in `agent/context.py`)
- **Windows** — `search_repo` (not `search_files`) for repo exploration

## Project context (auto-injected)

`load_project_context()` in `agent/project_context.py` adds a **# Project context** section to the system prompt (budget ~12 KB, priority-trimmed):

1. `README.md`
2. Build manifests (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Makefile`, `CMakeLists.txt`)
3. First `.github/workflows/*.yml` snippet
4. Test layout summary (`tests/`, `test/`, …)
5. Shallow **repo map** (depth 2; skips `node_modules`, `.git`, …)
6. Project skill names under `.agent-cli/skills/`

**AGENTS.md** is injected separately via `load_project_rules()` — not duplicated in project context.

## First-run scaffold (`agent init`)

`agent/init_scaffold.py` defaults for new projects:

| Setting | Default |
|---------|---------|
| `approval_mode` | `interactive` |
| `[memories] enabled` | `true` (`.agent-cli/memories.json`) |
| `[web_search] enabled` | `true` (DuckDuckGo) |
| `[harness] post_patch_test` | Auto-detected (`pytest -q`, `npm test`, `cargo test`, `go test ./...`) |

Existing repos keep their config until you edit it or run `agent init --yes`.

## Related

- [codex-comparison.md](codex-comparison.md) — harness parity matrix
- [context.md](context.md) — token metering and compaction
- [roadmap/phase-26.md](roadmap/phase-26.md) — `post_patch_test` hook
