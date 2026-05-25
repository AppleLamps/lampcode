# Phase 26 — OpenRouter differentiation (target v2.7.0)

**Goal:** Features Codex cannot easily offer: model intelligence, budget control, CI-grade review, and harness observability.

**Prerequisite:** Phase 25 complete (v2.6.0).

---

## Step 0 — Baseline

1. `pytest -q` green on v2.6.0.
2. Review OpenRouter UX already shipped: routing, cost, doctor probe, tool warnings.
3. Skim: `agent/providers/openrouter.py`, `agent/model_routing.py`, `agent/review.py`, `agent/json_stream.py`.

---

## Step 1 — Model preflight in doctor + run

**Why:** OpenRouter model catalog varies; users pick non-tool models and waste turns.

### 1.1 Design

- `agent doctor --models` table per configured profile:
  - Tool calling support (heuristic + optional OpenRouter query)
  - Context window estimate
  - $/1M in/out from pricing seed
  - Vision / reasoning flags
- `agent run` / REPL: warn once per session if selected model lacks tools.

### 1.2 Implement

| File | Change |
|------|--------|
| `agent/providers/openrouter.py` | `fetch_model_metadata` or cache from models API |
| `agent/skills/doctor.py` or `cli/main.py` | Extended doctor rows |
| `agent/loop.py` | Preflight warn before first completion |

### 1.3 Tests

- `tests/test_phase26.py::test_doctor_model_table_includes_tool_support`
- `tests/test_phase26.py::test_run_warns_on_non_tool_model` (mock)

### 1.4 Commit

```
feat(openrouter): model preflight in doctor and run warnings
```

### 1.5 Done when

- `agent doctor` shows tool-capable column for default profile model.
- Mocked non-tool model emits single stderr warning on run.

---

## Step 2 — Budget caps per run / thread

**Why:** Codex ties billing to ChatGPT; OpenRouter users need spend guardrails.

### 2.1 Design

- Flags: `agent run --max-cost 0.50`, config `[budget] max_cost_usd_per_turn`
- Track cumulative `estimated_cost_usd` during turn; stop gracefully with partial summary.
- `agent threads cost` already exists — align semantics.

### 2.2 Implement

| File | Change |
|------|--------|
| `agent/settings.py` | `BudgetSettings` |
| `agent/loop.py` | Check after each completion round |
| `cli/main.py` | `--max-cost` flag |
| `agent/output_handler.py` | `[done] budget_exceeded=true` in summary |

### 2.3 Tests

- `tests/test_phase26.py::test_turn_stops_when_max_cost_exceeded`

### 2.4 Commit

```
feat(budget): max cost per turn with graceful stop
```

### 2.5 Done when

- Mocked escalating cost stops loop before `max_tool_rounds` with clear message.

---

## Step 3 — Review JSON schema v1 + CI exit codes

**Why:** Make `agent review --json` a drop-in CI step.

### 3.1 Design

- Stable schema `review.v1.json` in repo (JSON Schema file).
- `--schema-version v1` default on `--json`.
- `--fail-on critical,major` → exit code 1 if findings at or above threshold.
- `--severity-threshold major` alias.

### 3.2 Implement

| File | Change |
|------|--------|
| `schemas/review.v1.json` | New JSON Schema |
| `agent/review.py` | `review_report_to_json` version field |
| `cli/main.py` | `--fail-on`, exit code mapping |
| `docs/roadmap/examples/review-ci.yml` | Optional GitHub Actions snippet |

### 3.3 Tests

- `tests/test_phase26.py::test_review_json_matches_schema`
- `tests/test_phase26.py::test_review_exit_code_on_critical`

### 3.4 Commit

```
feat(review): review.v1 schema and CI fail-on severity exit codes
```

### 3.5 Done when

- JSON validates against schema in test.
- Mock critical finding → exit code 1 with `--fail-on critical`.

---

## Step 4 — JSON stream parity audit

**Why:** CI consumers need stable event names and ordering vs Codex `--json`.

### 4.1 Design

- Document event catalog in `docs/json-events.md`.
- Align naming where cheap: `thread.started`, `turn.completed`, `tool.started`, `tool.completed`, `run.summary`.
- Add missing events if gaps found (e.g. `compaction.completed` already exists — verify fields).

### 4.2 Implement

| File | Change |
|------|--------|
| `agent/json_stream.py` | Field normalization pass |
| `docs/json-events.md` | Event catalog + examples |
| `tests/test_json_stream.py` | Golden file fixture for one mocked run |

### 4.3 Commit

```
docs(json): event catalog and stream parity fixtures
```

### 4.4 Done when

- Snapshot test of one full mocked `--json` run matches committed fixture.
- `docs/json-events.md` lists all event types.

---

## Step 5 — Post-patch test hook (opt-in harness)

**Why:** Codex relies on the model to re-run tests; harness can nudge reliability.

### 5.1 Design

- Config: `[harness] post_patch_test = "pytest -q"` (empty = off).
- After successful `apply_patch` approval, optionally run command once; inject output as system note or tool result.
- Never auto-run in `--plan` or read-only review.

### 5.2 Implement

| File | Change |
|------|--------|
| `agent/settings.py` | `HarnessSettings.post_patch_test` |
| `agent/loop.py` | Hook after file change item completed |
| `agent/init_scaffold.py` | Commented example |

### 5.3 Tests

- `tests/test_phase26.py::test_post_patch_test_runs_after_apply` (mock subprocess)

### 5.4 Commit

```
feat(harness): optional post-patch test command
```

### 5.5 Done when

- Enabled config runs mock command after patch; disabled does not.

---

## Step 6 — Turn observability summary

**Why:** Beat Codex on transparency for OpenRouter users.

### 6.1 Design

- End-of-turn enrichment: files touched (+/− lines), commands run count, tests detected in output.
- `agent threads show <id> --stats`
- Include in `[done]` line and `--json` run.summary.

### 6.2 Implement

| File | Change |
|------|--------|
| `agent/turn_stats.py` | Aggregate from turn items |
| `cli/main.py` | `--stats` on threads show |
| `agent/output_handler.py` | Extended done line |

### 6.3 Tests

- `tests/test_phase26.py::test_turn_stats_from_mixed_items`

### 6.4 Commit

```
feat(stats): turn and thread stats for files, commands, and tests
```

---

## Step 7 — Docs and release

1. `docs/codex-comparison.md` — OpenRouter differentiation section expanded.
2. `docs/session-context.md` → Phase 26 complete; sketch Phase 27 (ConPTY, mcp-server, web search).
3. `CHANGELOG.md` + `pyproject.toml` → **2.7.0**.
4. `README.md` — budget, review CI, post-patch test one-liners.

### Commit

```
chore(release): v2.7.0 Phase 26 OpenRouter differentiation
```

### Phase 26 exit criteria

- [ ] `pytest -q` green (target ≥980 tests)
- [ ] `schemas/review.v1.json` present and tested
- [ ] `docs/json-events.md` published
- [ ] Golden path E2E green

---

## Phase 27 candidates (not specified here)

- ConPTY / interactive PTY on Windows
- `agent mcp-server` (expose harness as MCP tool)
- Web search provider upgrade (Exa/Tavily config slot)
- Exec policy amendments (session-scoped allow rules without Starlark)
- JSON replay bundle export compatible with internal debug workflows
