# Phase 27 — Terminal & embed (target v2.8.0)

**Goal:** Close the biggest Codex mechanics gaps for daily use on Windows and make the harness embeddable in external MCP clients — without porting Starlark, cloud memories, or the full native sandbox stack.

**Prerequisite:** Phase 26 complete (v2.7.0).

---

## Step 0 — Baseline

1. `pytest -q` green on v2.7.0 (~953 passed, 1 skipped).
2. Read Codex: `codex-rs/core/src/unified_exec/`, `codex-rs/utils/pty/`, `codex-rs/windows-sandbox-rs/src/conpty/`, `codex-rs/mcp-server/src/codex_tool_config.rs`, `codex-rs/execpolicy/src/amend.rs`, `codex-rs/exec/src/cli.rs` (`ReviewArgs`).
3. Skim: `agent/execution/shell_session.py`, `tools/web_search.py`, `agent/exec_policy.py`, `agent/review.py`, `cli/main.py`.

---

## Step 1 — Windows ConPTY / unified exec v2

**Why:** Codex uses PTY-backed persistent shells (`process_id`, `write_stdin`, yield/output caps). agent-cli uses **pipe-persistent** on Windows — interactive TTY semantics (colors, pagers, progress bars) are weaker than Codex on your daily OS.

### 1.1 Design

- Optional ConPTY backend when `[shell] enabled = true` on Windows.
- Unified session API across backends:
  - `session_id`, `write_stdin`, `yield_ms`, `max_output_chars`
  - Backend values: `oneshot` | `pipes` | `pty` | `conpty`
- Probe in `pty_support_status()`; doctor row shows active backend + fallback note.
- Preserve pipe-persistent fallback when ConPTY unavailable.

### 1.2 Implement

| File | Change |
|------|--------|
| `agent/execution/conpty.py` | New: ConPTY spawn/read/write probe (Windows-only) |
| `agent/execution/shell_session.py` | Route Windows to ConPTY when enabled; unify stdin/yield |
| `agent/settings.py` | `[shell] backend = "auto"` (`auto` \| `pipes` \| `conpty` \| `pty`) |
| `cli/main.py` | Doctor shell row: backend + ConPTY availability |
| `agent/init_scaffold.py` | Commented `[shell]` backend example |

### 1.3 Tests

- `tests/test_phase27.py::test_conpty_probe_reports_unavailable_off_windows` (skip on Windows if no ConPTY)
- `tests/test_phase27.py::test_shell_session_backend_auto_selects_pipes_on_windows_without_conpty`
- `tests/test_phase27.py::test_shell_session_write_stdin_round_trip` (mock subprocess)

### 1.4 Commit

```
feat(shell): Windows ConPTY backend and unified exec v2 session API
```

### 1.5 Done when

- `agent doctor` reports shell backend (`conpty`, `pipes`, or `pty`) on Windows.
- `[shell] enabled = true` uses ConPTY when probe succeeds; falls back to pipes otherwise.
- Existing Unix PTY path unchanged.

---

## Step 2 — `agent mcp-server`

**Why:** Codex exposes `codex mcp-server` so IDEs and automation can invoke the agent as an MCP tool. agent-cli is MCP-client-only today.

### 2.1 Design

- Stdio MCP server command: `agent mcp-server`.
- Expose tool `agent` (or `agent_run`) with schema:
  - `prompt` (required), `cwd`, `model`, `sandbox_mode`, `auto_approve`, `max_cost`, `profile`
- Run one turn via existing `run_turn` / headless path; return structured result (status, final text, cost, thread_id).
- Approvals: `auto_approve=true` for CI embeds; optional MCP elicitation stub for interactive (v1 can document headless-only).
- Never expose enterprise serve/OIDC surface.

### 2.2 Implement

| File | Change |
|------|--------|
| `agent/mcp_server/` | New package: stdio loop, tool schema, runner |
| `agent/mcp_server/runner.py` | Bridge to `run_turn` with ephemeral or temp thread |
| `cli/main.py` | `mcp-server` subcommand |
| `README.md` | One-liner embed example for Cursor/Claude Desktop |

### 2.3 Tests

- `tests/test_phase27.py::test_mcp_server_tool_schema_lists_agent_run`
- `tests/test_phase27.py::test_mcp_server_run_turn_mocked_openrouter` (mock client + auto_approve)
- `tests/test_phase27.py::test_mcp_server_respects_max_cost_flag`

### 2.4 Commit

```
feat(mcp): agent mcp-server stdio tool for external clients
```

### 2.5 Done when

- `agent mcp-server` starts and responds to `tools/list` with the agent tool.
- Mocked tool call completes one turn and returns JSON result with `status` and `cost`.
- OpenRouter + `--max-cost` overrides pass through tool arguments.

---

## Step 3 — Web search provider slot

**Why:** Codex uses hosted Responses web search (OpenAI infra). OpenRouter harness needs pluggable quality providers — DuckDuckGo alone is weak for research tasks.

### 3.1 Design

- Provider interface in `tools/web_search.py`:
  - `duckduckgo` (default, no key)
  - `exa` (API key via `EXA_API_KEY` or config)
  - `tavily` (API key via `TAVILY_API_KEY` or config)
- Config:
  ```toml
  [web_search]
  enabled = true
  provider = "exa"
  api_key_env = "EXA_API_KEY"
  max_results = 5
  ```
- Doctor row: provider name + key present/missing + optional reachability probe (mock in tests).
- Keep sandbox block for read-only mode; parallel read batching unchanged.

### 3.2 Implement

| File | Change |
|------|--------|
| `tools/web_search.py` | Provider dispatch + Exa/Tavily HTTP clients |
| `agent/settings.py` | `WebSearchSettings.api_key_env`, provider validation |
| `agent/config.py` | Wire settings |
| `cli/main.py` | Doctor web search row |

### 3.3 Tests

- `tests/test_phase27.py::test_web_search_unknown_provider_returns_error`
- `tests/test_phase27.py::test_web_search_exa_mocked_response`
- `tests/test_phase27.py::test_web_search_tavily_mocked_response`
- `tests/test_phase27.py::test_doctor_web_search_row_shows_provider`

### 3.4 Commit

```
feat(web): pluggable Exa and Tavily web search providers
```

### 3.5 Done when

- `[web_search] provider = "exa"` with mocked HTTP returns structured results.
- Missing API key yields clear tool error, not a crash.
- DuckDuckGo remains default when provider unset.

---

## Step 4 — Exec-policy session amendments

**Why:** Codex approval UI can append allow-prefix rules to policy on approve (`execpolicy/src/amend.rs`). agent-cli has session approval cache but no persistent amend from approval flow.

### 4.1 Design

- TOML-only — **no Starlark**.
- On approval prompt for `run_command`, optional response:
  - `y` — approve once (existing)
  - `a` / `A` — existing turn/session auto-approve
  - **`p`** — approve and append command prefix to project allow list (new)
- File: `{cwd}/.agent-cli/exec-policy.toml` under `[allow_prefixes]` (or extend existing exec_policy section).
- CLI: `agent exec-policy amend --prefix "pytest -q"` for manual append.
- Dedupe prefixes; normalize whitespace.

### 4.2 Implement

| File | Change |
|------|--------|
| `agent/exec_policy.py` | Load/save allow_prefixes; evaluate before glob rules |
| `approval/gate.py` | Parse `p` response; call amend helper |
| `cli/main.py` | `exec-policy amend` subcommand |
| `agent/init_scaffold.py` | Commented exec-policy amend example |

### 4.3 Tests

- `tests/test_phase27.py::test_exec_policy_allow_prefix_auto_approves_matching_command`
- `tests/test_phase27.py::test_exec_policy_amend_cli_appends_prefix`
- `tests/test_phase27.py::test_exec_policy_amend_dedupes_prefix`

### 4.4 Commit

```
feat(exec-policy): session approval amend for command prefix allow list
```

### 4.5 Done when

- After amend, identical command prefix skips approval prompt in same project.
- `agent exec-policy amend --prefix "git status"` persists to project config.
- Starlark / `.codexpolicy` not introduced.

---

## Step 5 — Review polish (custom prompt + merge-base)

**Why:** Codex `ReviewArgs` supports a positional custom prompt and merge-base SHA for `--base` reviews (`core/src/review_prompts.rs`). agent-cli has three modes but lacks custom instructions and explicit merge-base metadata.

### 5.1 Design

- `agent review "focus on security"` — custom instructions appended to review system prompt.
- `agent review -` — read custom prompt from stdin.
- `--base main` uses `git merge-base HEAD main` for diff anchor (not only `{ref}...HEAD`).
- Include in `--json` output: `review_scope`, `merge_base_sha` when applicable.
- Extend `schemas/review.v1.json` with optional fields (backward compatible).

### 5.2 Implement

| File | Change |
|------|--------|
| `agent/review.py` | `merge_base` helper; custom prompt in context |
| `agent/review_runner.py` | Pass custom prompt through |
| `cli/main.py` | Positional prompt arg; stdin `-` |
| `schemas/review.v1.json` | Optional `merge_base_sha`, `review_scope` |

### 5.3 Tests

- `tests/test_phase27.py::test_review_custom_prompt_in_system_context`
- `tests/test_phase27.py::test_review_base_uses_merge_base_for_diff`
- `tests/test_phase27.py::test_review_json_includes_merge_base_sha`

### 5.4 Commit

```
feat(review): custom prompt and merge-base diff for base reviews
```

### 5.5 Done when

- Positional prompt changes review focus in mocked run.
- `--base main` diff uses merge-base SHA when git available (mock git in tests).
- Phase 26 `--fail-on` and schema_version unchanged.

---

## Step 6 — Docs and release

1. `docs/codex-comparison.md` — ConPTY, mcp-server, web search, exec-policy amend, review merge-base rows.
2. `docs/session-context.md` → Phase 27 complete; sketch Phase 28 (replay bundle, hooks v2).
3. `CHANGELOG.md` + `pyproject.toml` → **2.8.0**.
4. `README.md` — mcp-server, web search provider, exec-policy amend one-liners.

### Commit

```
chore(release): v2.8.0 Phase 27 terminal and embed
```

### Phase 27 exit criteria

- [ ] `pytest -q` green (target ≥970 tests)
- [ ] `agent doctor` shows ConPTY/pipes backend on Windows
- [ ] `agent mcp-server` tool call works with mocked OpenRouter
- [ ] At least one non-DuckDuckGo web search provider tested
- [ ] Golden path E2E green

---

## Explicitly out of scope (Phase 27)

- Starlark execpolicy / `.codexpolicy` files
- Full Codex Windows sandbox (WFP, elevated runner)
- Codex hosted web search / Responses API search actions
- Cloud memories pipeline
- REPL/TUI parity with Codex ratatui (resume picker from Phase 25 is enough)

## Phase 28 preview

See [phase-28.md](phase-28.md) — replay bundle export, hooks lifecycle v2, memories v3, plan mode proposed_plan, doctor JSON.
