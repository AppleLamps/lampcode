# Changelog

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
