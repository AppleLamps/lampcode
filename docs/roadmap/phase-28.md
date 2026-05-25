# Phase 28 — Observability & trust (target v2.9.0)

**Goal:** Match Codex debuggability and hook lifecycle where it helps solo users — local replay bundles, richer hooks, smarter local memories — while keeping the harness OpenRouter-first and REPL-centric.

**Prerequisite:** Phase 27 complete (v2.8.0).

---

## Step 0 — Baseline

1. `pytest -q` green on v2.8.0 (~970+ passed, 1 skipped).
2. Read Codex: `codex-rs/rollout-trace/README.md`, `codex-rs/hooks/src/events/`, `codex-rs/hooks/src/schema.rs`, `codex-rs/core/src/hook_runtime.rs`, `codex-rs/utils/stream-parser/src/proposed_plan.rs`, `codex-rs/cli/src/doctor/`.
3. Skim: `agent/recording/`, `agent/hooks/runner.py`, `agent/memories.py`, `agent/compaction.py`, `cli/main.py` doctor + runs export.

---

## Step 1 — Replay bundle export

**Why:** Codex `rollout-trace` writes local debug bundles (thread + raw events + reduced state) for post-mortems. agent-cli has `runs export --format jsonl-v2` but no single tarball for sharing or offline debug.

### 1.1 Design

- `agent runs export <turn-prefix> --format bundle --out run.bundle.tar.gz`
- Bundle contents:
  - `manifest.json` — schema_version, agent version, thread_id, turn_id, model, cost, timestamp
  - `thread.jsonl` — thread snapshot (or slice for turn)
  - `events.jsonl` — normalized run events for turn
  - `config.redacted.json` — resolved config with secrets stripped
- Optional: `agent runs bundle-info <file>` — print manifest without extracting.
- **Skip** Codex rollout-trace semantic graph reducer for v1.

### 1.2 Implement

| File | Change |
|------|--------|
| `agent/recording/bundle.py` | New: pack/unpack tarball, manifest schema |
| `schemas/run.bundle.v1.json` | JSON Schema for manifest |
| `cli/main.py` | `--format bundle` on `runs export`; `runs bundle-info` |
| `docs/run-bundle.md` | Bundle layout + privacy note (local only) |

### 1.3 Tests

- `tests/test_phase28.py::test_run_bundle_export_contains_manifest_and_events`
- `tests/test_phase28.py::test_run_bundle_redacts_api_keys`
- `tests/test_phase28.py::test_run_bundle_info_reads_manifest_without_full_extract`
- `tests/test_phase28.py::test_run_bundle_manifest_matches_schema`

### 1.4 Commit

```
feat(runs): export debug replay bundle tarball with redacted config
```

### 1.5 Done when

- Export produces valid tar.gz; manifest validates against schema in test.
- `OPENROUTER_API_KEY` and similar never appear in bundle files.
- Existing `jsonl-v2` and markdown export unchanged.

---

## Step 2 — Hooks lifecycle v2

**Why:** Codex hooks cover SessionStart, UserPromptSubmit, PreToolUse (block), PermissionRequest, PostToolUse, Stop, and compact events with structured JSON I/O. agent-cli has four subprocess hook events only.

### 2.1 Design

Add hook events (subprocess JSON stdin/stdout, same runner pattern):

| Event | When | Output semantics |
|-------|------|------------------|
| `on_session_start` | Thread/run begins | Informational only (v1) |
| `on_user_prompt_submit` | Before turn loop | Optional `context_append` string in stdout JSON |
| `on_permission_request` | Before approval prompt | Optional `context_append` for approval UI |
| `on_pre_tool_use` | After tool pending, before execute | `{ "decision": "block", "reason": "..." }` to deny |

Keep existing: `on_tool_pending`, `on_turn_completed`, `on_pre_compact`, `on_post_compact`.

Document in `agent/hooks/README.md` or extend init scaffold comment block.

**Skip for v1:** SubagentStop, hook trust-review TUI, full JSON Schema codegen per event.

### 2.2 Implement

| File | Change |
|------|--------|
| `agent/hooks/runner.py` | New event dispatch; parse block decision from stdout |
| `agent/loop.py` | Fire hooks at session start, prompt submit, permission, pre-tool |
| `agent/hooks/schema.md` | Payload shapes for each event |
| `cli/main.py` | `agent hooks list` shows all supported events |

### 2.3 Tests

- `tests/test_phase28.py::test_hooks_session_start_fires_subprocess`
- `tests/test_phase28.py::test_hooks_pre_tool_use_block_skips_dispatch`
- `tests/test_phase28.py::test_hooks_user_prompt_submit_context_append`
- `tests/test_phase28.py::test_hooks_list_includes_v2_events`

### 2.4 Commit

```
feat(hooks): SessionStart, UserPromptSubmit, PermissionRequest, PreToolUse block
```

### 2.5 Done when

- Hook returning `{"decision":"block"}` prevents tool execution with clear tool result message.
- `agent hooks list` documents all eight events.
- `fail_on_error` behavior unchanged from Phase 22.

---

## Step 3 — Memories v3 (local)

**Why:** Codex uses a cloud memory consolidation pipeline. agent-cli has local v2 scoring — improve retrieval and review workflow without external APIs.

### 3.1 Design

- **Suggest queue:** At turn end, if `[memories] auto_suggest = true`, queue candidate memory (existing heuristic + cap).
- `agent memories suggest` — list pending suggestions for cwd.
- `agent memories accept <id>` / `reject <id>` — promote or discard.
- **Inject scoring v3:** keyword overlap with user prompt + tag match + recency decay (no embeddings).
- `agent memories inject --dry-run` — show what would be injected for a prompt string.
- Cap injected chars; memories path sandbox allowlist unchanged (Phase 25).

### 3.2 Implement

| File | Change |
|------|--------|
| `agent/memories.py` | Suggest queue, accept/reject, scoring v3, dry-run inject |
| `agent/settings.py` | `MemoriesSettings.auto_suggest`, `suggest_max_pending` |
| `agent/loop.py` | Optional suggest on turn complete |
| `cli/main.py` | `memories suggest|accept|reject|inject --dry-run` |

### 3.3 Tests

- `tests/test_phase28.py::test_memories_suggest_queue_accept_promotes_to_store`
- `tests/test_phase28.py::test_memories_inject_dry_run_respects_char_cap`
- `tests/test_phase28.py::test_memories_scoring_prefers_tag_and_keyword_match`

### 3.4 Commit

```
feat(memories): suggest queue, accept/reject, and inject dry-run
```

### 3.5 Done when

- Suggested memory stays pending until `memories accept`.
- `inject --dry-run` never writes to store.
- No outbound HTTP for memory consolidation.

---

## Step 4 — Plan mode v2 (`<proposed_plan>`)

**Why:** Codex plan/collaboration modes parse `<proposed_plan>` blocks and show mode in the composer footer. agent-cli filters tools in plan mode but does not surface structured plans.

### 4.1 Design

- Parse `<proposed_plan>...</proposed_plan>` from agent message text → `PlanProposalItem` (or extend agent message metadata).
- REPL `/plan` shows: mode on/off, allowed tools list, last proposed plan summary if any.
- `agent run --plan --json` emits `plan.proposed` event when block parsed.
- Plan mode still blocks writes via existing `[plan_mode]` tool filter.

**Skip:** Full collaboration mode matrix (per-mode model/reasoning/developer_instructions).

### 4.2 Implement

| File | Change |
|------|--------|
| `agent/plan_mode.py` | New: parse proposed_plan, strip from display text |
| `agent/models.py` | Optional `PlanProposalItem` type |
| `agent/json_stream.py` | `plan.proposed` event |
| `agent/repl.py` | `/plan status` output |
| `docs/json-events.md` | Document `plan.proposed` |

### 4.3 Tests

- `tests/test_phase28.py::test_plan_mode_parses_proposed_plan_block`
- `tests/test_phase28.py::test_plan_mode_json_emits_plan_proposed_event`
- `tests/test_phase28.py::test_plan_mode_write_tools_still_blocked`

### 4.4 Commit

```
feat(plan): parse proposed_plan blocks and plan.proposed JSON event
```

### 4.5 Done when

- Agent output with plan tags yields structured item in thread JSONL.
- `--json` run includes `plan.proposed` once per parsed block.
- `apply_patch` remains blocked in plan mode.

---

## Step 5 — Doctor v2 (harness-focused)

**Why:** Codex doctor checks sandbox, terminal, MCP, search, git, config, and more. agent-cli doctor is OpenRouter-strong but narrow — extend for Phase 27–28 features without ChatGPT-specific checks.

### 5.1 Design

- `agent doctor --json` — machine-readable report (array of `{check, status, detail}`).
- New rows when relevant:
  - Shell backend (conpty/pipes/pty)
  - Web search provider + key status
  - Exec-policy file + allow_prefix count
  - Hooks configured (count by event)
  - Memories enabled + pending suggest count
  - Kernel sandbox probe (existing, ensure JSON includes it)

**Skip:** ChatGPT auth, websocket, app-server, self-update checks.

### 5.2 Implement

| File | Change |
|------|--------|
| `cli/main.py` | `--json` on doctor; assemble report struct |
| `agent/doctor/report.py` | New: build check list (optional extract from main) |

### 5.3 Tests

- `tests/test_phase28.py::test_doctor_json_includes_openrouter_and_shell_checks`
- `tests/test_phase28.py::test_doctor_json_exit_code_missing_api_key`

### 5.4 Commit

```
feat(doctor): --json machine-readable harness diagnostics report
```

### 5.5 Done when

- `agent doctor --json` parses as JSON array/object in test.
- Rich table output unchanged when `--json` not passed.
- OpenRouter preflight table (`--models`) still works.

---

## Step 6 — Compaction tuning

**Why:** Codex triggers mid-turn compaction when context grows. agent-cli auto-compacts at threshold; add explicit controls and REPL force.

### 6.1 Design

- Config: `[compaction] auto_mid_turn = true` (default true), `token_threshold` (reuse or alias existing).
- REPL: `/compact` force compact current thread (respect hooks).
- Emit `compaction.warning` when second+ compact in same turn (existing) — verify still fires after mid-turn path.

**Skip:** Remote/provider-side compaction (OpenAI-specific).

### 6.2 Implement

| File | Change |
|------|--------|
| `agent/compaction.py` | `force_compact_thread()` for REPL |
| `agent/settings.py` | `CompactionSettings.auto_mid_turn` |
| `agent/repl.py` | `/compact` command |
| `agent/loop.py` | Respect `auto_mid_turn` flag |

### 6.3 Tests

- `tests/test_phase28.py::test_repl_compact_command_reduces_context` (mock client)
- `tests/test_phase28.py::test_compaction_auto_mid_turn_disabled_skips_mid_turn`
- `tests/test_phase28.py::test_compaction_double_compact_emits_warning` (extend Phase 24 pattern)

### 6.4 Commit

```
feat(compaction): REPL /compact and auto_mid_turn config toggle
```

### 6.5 Done when

- `/compact` in REPL triggers compaction with mocked thread over threshold.
- `[compaction] auto_mid_turn = false` skips mid-turn compact only; manual `/compact` still works.

---

## Step 7 — Docs and release

1. `docs/codex-comparison.md` — replay bundle, hooks v2, memories v3, plan proposed_plan, doctor JSON.
2. `docs/session-context.md` → Phase 28 complete; sketch Phase 29 candidates (ConPTY polish, MCP elicitation, web search open-page).
3. `CHANGELOG.md` + `pyproject.toml` → **2.9.0**.
4. `README.md` — bundle export, hooks events, memories suggest one-liners.
5. Update `docs/json-events.md` for `plan.proposed`.

### Commit

```
chore(release): v2.9.0 Phase 28 observability and trust
```

### Phase 28 exit criteria

- [ ] `pytest -q` green (target ≥990 tests)
- [ ] `schemas/run.bundle.v1.json` present and tested
- [ ] `agent runs export --format bundle` produces valid tarball
- [ ] Hooks v2 block decision tested
- [ ] Golden path E2E green

---

## Explicitly out of scope (Phase 28)

- Codex rollout-trace semantic reducer / inference graph
- Cloud memory API / `/memories/trace_summarize`
- Full hook JSON Schema codegen + trust-review TUI
- Collaboration mode matrix beyond plan filter
- Enterprise replay federation / serve export
- Starlark execpolicy

## Phase 29 candidates (not specified here)

- MCP server approval elicitation (interactive embed)
- Web search `open_page` read-only fetch action
- ConPTY resize + terminal size propagation
- `agent runs import-bundle` replay into read-only viewer
- Kernel sandbox doctor-guided opt-in wizard
