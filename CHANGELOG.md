# Changelog

## [2.7.0] — 2026-05-25

Phase 26 OpenRouter differentiation (solo only).

### Added

- **Model preflight:** `agent doctor --models` table (tools, context, pricing, vision, reasoning); once-per-session tool-capability warning on run.
- **Budget caps:** `agent run --max-cost`, config `[budget] max_cost_usd_per_turn`; graceful turn stop with `budget_exceeded` in `[done]` / `--json`.
- **Review CI schema:** `schemas/review.v1.json`; `--fail-on` / `--severity-threshold` exit codes for `agent review --json`.
- **JSON stream parity:** `docs/json-events.md` catalog; golden fixture for event ordering.
- **Post-patch test hook:** `[harness] post_patch_test` runs after successful `apply_patch`.
- **Turn stats:** `agent threads show --stats`; files touched, lines +/-, commands run, tests detected in `[done]` and `run.summary`.

### Changed

- **Review JSON:** `schema_version` field (default `v1`) on structured review output.
- **HarnessSession:** Restored `approval_cache` alongside turn cost tracking.

### Tests

- `tests/test_phase26.py` — **953** pytest cases (944 baseline + 9 new).

## [2.6.0] — 2026-05-25

Phase 25 daily UX (solo only).

### Added

- **Thread picker:** `agent threads pick`, `agent run --resume`, `agent repl --resume`; REPL `/resume`, `/fork`, `/resume last`.
- **`agent apply`:** Re-apply last agent patch from a thread (`--dry-run`, `--thread-id`).
- **`--ephemeral` runs:** Non-persistent one-shot runs and REPL `/ephemeral on` (no JSONL under `~/.agent-cli/threads/`).
- **Turn notifications:** `[notify] command` with `AGENT_THREAD_ID`, `AGENT_TURN_ID`, `AGENT_STATUS`, `AGENT_CWD`.
- **Memories v2:** Scoring by tags + recency + cwd; improved inject; memories path allowed in workspace-write sandbox.

### Changed

- **`find_latest_for_cwd`:** Uses `updated_at` instead of file mtime (aligned with REPL `--resume-last`).
- **`resolve_user_input`:** Supports injected `input_fn` without TTY (picker/tests).

### Tests

- `tests/test_phase25.py` — **944** pytest cases (934 baseline + 10 new).

## [2.5.0] — 2026-05-25

Phase 24 reliability core (solo only).

### Added

- **Unified exec v1:** `stdin`, `max_output_chars`, `yield_ms`, and session meta on `run_command`; truncation marker in output.
- **Approval cache:** Session-scoped cache skips re-prompt for identical approved commands; per-file keys for `apply_patch`.
- **Sandbox retry escalation:** One automatic retry after user-approved sandbox denial when escalation is possible.
- **Compaction v2:** AGENTS.md + original task in compact prompt; `on_pre_compact` / `on_post_compact` hooks; multi-compact accuracy warning.
- **Parallel read-only tools:** Concurrent dispatch for `read_file`, `search_repo`, and `web_search` within a round (configurable cap).

### Changed

- **Doctor:** Shell output limits and harness parallelism rows.
- **Loop orchestration:** Exec policy → approval → sandbox precheck with retry; parallel read batching when an entire round is read-only.

### Tests

- `tests/test_phase24.py` — **934** pytest cases (924 baseline + 10 new).

## [2.4.0] — 2026-05-25

Phase 23 harness hardening (solo only).

### Added

- **Windows persistent shell:** Pipe-based `cmd.exe` sessions when `[shell] enabled = true`; completion markers for reliable output boundaries (no ConPTY required).
- **Compaction regression test:** Proves task marker survives compact + `build_thread_messages` rebuild.
- **Review `--json` CI fixture:** Structured findings payload for scripted review runs.

### Changed

- **Doctor / PTY status:** Windows reports `persistent-pipes` backend instead of one-shot-only fallback.
- **Session context:** Phase 23 direction and v2.3.0 baseline in `docs/session-context.md`.

### Tests

- `tests/test_phase23.py` — persistent shell, compaction resume context, review JSON fixture.

## [2.3.0] — 2026-05-25

Phase 22 Codex harness parity (solo only).

### Added

- **`agent review`:** `--uncommitted`, `--base`, `--commit`; read-only sandbox; structured markdown + `--json` report.
- **`agent run --json`:** Normalized Codex-like JSONL event stream (`agent/json_stream.py`); `--jsonl-events` unchanged.
- **`request_user_input` tool:** Mid-turn structured questions; REPL/TTY + `AGENT_INPUT_ANSWERS` for CI.
- **`request_permissions` tool:** Mid-turn sandbox escalation (network / write-outside-cwd / full_access).
- **Plan mode:** `agent run --plan`, REPL `/plan on|off`; filters tools via `[plan_mode]`.
- **Hooks:** `{cwd}/.agent-cli/hooks.json`, `agent hooks list|test`.
- **Persistent shell (opt-in):** `[shell] enabled = true`; one session per thread; doctor PTY row.
- **`--output-schema`:** Validated JSON final output on `agent run`.
- **Memories v1:** `agent memories list|add|delete|search`; opt-in `[memories] enabled`.
- **`agent runs export --format jsonl-v2`:** Normalized replay from run logs.

### Tests

- `tests/test_review.py`, `tests/test_json_stream.py`, `tests/test_phase22.py` — **920** pytest cases (912 new + existing).

## [2.2.0] — 2026-05-25

Phase 21 harness gaps (solo only — no new serve/OIDC/RBAC/scheduler features).

### Added

- **`agent threads pr-description`:** Bullet summary (`summarize_thread_for_pr`), `--summary-only` flag.
- **Single-agent turn checkpoint/resume:** `[turn_checkpoint]` config; save on cancel; `--resume-turn`, `agent threads resume-turn`, `agent threads checkpoint-status`.
- **REPL tab completion:** `@skill` prefix filter + `/commands` via readline when available.
- **Patch UX:** `preview_patch`, approval/run summaries with +/− stats and diff preview on `apply_patch`.
- **Doctor:** Split OpenRouter reachability vs API key validity checks.

### Tests

- `tests/test_phase21.py` — coverage for each Phase 21 feature.

## [2.1.0] — 2026-05-25

Phase 20 solo-first release: golden-path harness polish, REPL/TUI parity, OpenRouter UX, docs split, and test hardening. **No Phase 21 / new enterprise features.**

### Added

- **Golden path:** `examples/demo-project` with deliberate bug, `setup.ps1`, project config, pytest-fix skill, and mocked E2E test.
- **Model routing:** `[model_routing]` rules auto-select `model_profile` from task text (REPL/TUI/run).
- **`.env` loading:** `OPENROUTER_API_KEY` and other vars from cwd/git-root/package `.env` at startup.
- **Cost tracking:** Default OpenRouter pricing seed; non-zero `estimated_cost_usd` on turns and `agent threads cost`.
- **OpenRouter UX:** Tool-calling warnings for unsupported models; fallback chain and retry surfaced in run summary (`[done] model=… cost≈$…`).
- **REPL/TUI:** Profile commands, turn summaries with cost, model-profile resolution in TUI runner.
- **`agent threads pr-description`:** PR body from thread transcript + git diff stat.
- **`agent doctor`:** Solo-dev readiness table + OpenRouter API reachability probe.
- **Docs:** [docs/codex-comparison.md](docs/codex-comparison.md), [docs/enterprise.md](docs/enterprise.md); README trimmed to solo quickstart.

### Fixed

- **Test isolation:** Autouse `conftest.py` resets approval globals; DAG tests use isolated registries (no cross-test flake).
- **Demo E2E:** Mocked OpenRouter tool loop fixes `calc.py` and asserts pytest green.

### Tests

- **852+** pytest cases (target ≥850); meaningful coverage for golden path, REPL/TUI routing, cost, and OpenRouter warnings.

### Unchanged

- Version remains **2.1.0** in `pyproject.toml`.
- Enterprise serve/OIDC/RBAC/DAG/scheduler docs moved to `docs/enterprise.md` (behavior unchanged, still opt-in).

## [2.0.0]

Phase 19 harness: OpenRouter provider v2, init/repl/profiles, Codex-style quickstart.

## [1.8.0]

Program sync, OAuth CAE webhooks, schedule notifications.
