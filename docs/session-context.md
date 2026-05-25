# Context: agent-cli harness project (read this first)

Paste this block at the start of Cursor sessions when working in the **Codex + lampcode** workspace.

## What I'm building

I'm building **agent-cli** — a **Python coding-agent harness** powered by **OpenRouter**, inspired by OpenAI's **Codex CLI** but **not** a fork of it and **not** using the official Codex binary or Rust runtime.

**North-star daily loop:**
> init repo → run agent → investigate with tools → edit files → run tests → summarize

**Design reference:** The `openai/codex` repo in this workspace (especially `codex-rs/`) is for **behavior and UX parity**, not runtime dependency.

**My project:** `lampcode/agent-cli` (`e:\lampcode\agent-cli` on Windows). Treat that as the implementation repo. Do **not** modify `openai/codex` unless I explicitly ask for read-only research.

---

## Current state (v2.3.0 — Phase 22 complete)

Phase 22 shipped as **v2.3.0**. Before doing new work, verify:

```powershell
cd e:\lampcode\agent-cli
pytest -q               # expect ~920 passed, 1 skipped, 0 failed
git log --oneline -15   # expect Phase 21 + Phase 22 logical commits after Phase 20
```

**Bottom line:** You're on **v2.3.0 / Phase 22 complete**. The next work is **Phase 23** (or polish on the two ⚠️ Phase 22 items below) — **not** the old Phase 21 backlog from earlier briefings.

### What's already done

**Golden path (Phase 20)**
- `examples/demo-project` with intentional bug (`calc.add` uses `-` not `+`)
- `agent init --yes`, idempotent init, `--skip-git-check`
- Mocked E2E: `tests/test_demo_project_e2e_mocked.py`

**Harness core (Phases 19–20)**
- `agent run`, `agent repl`, TUI, JSONL threads, approvals, compaction
- Tools: shell, apply_patch, read/list, MCP, skills, AGENTS.md
- Sandbox modes, exec policy, thread fork/resume
- Shared `agent/output_handler.py` for run + REPL streaming

**OpenRouter UX (Phase 20)**
- Model profiles (`--profile`, `--model-profile`)
- `agent/model_routing.py`, default pricing seed, cost tracking
- Tool-support warnings, doctor OpenRouter probe
- Run ends with: `[done] model=… fallback=… cost≈$… tokens in=… out=…`
- `.env` auto-load for `OPENROUTER_API_KEY`

**Phase 21 (v2.2.0)**
- `agent threads pr-description` — bullet summary, `--summary-only`
- Turn checkpoint/resume — `[turn_checkpoint]`, `--resume-turn`, `checkpoint-status`
- REPL `@skill` tab completion + `/commands` (readline when available)
- Patch UX — `preview_patch`, approval/run summaries with +/− stats
- Doctor — split OpenRouter reachability vs API key validity

**Phase 22 (v2.3.0)**
- **`agent review`** — `--uncommitted`, `--base`, `--commit`; read-only sandbox; `--json` report
- **`agent run --json`** — normalized Codex-like JSONL (`agent/json_stream.py`)
- **`request_user_input`** — mid-turn questions; REPL/TTY + `AGENT_INPUT_ANSWERS` for CI
- **`request_permissions`** — mid-turn sandbox escalation
- **Plan mode** — `agent run --plan`, REPL `/plan on|off`
- **Hooks** — `{cwd}/.agent-cli/hooks.json`, `agent hooks list|test`
- **Persistent shell (opt-in)** — `[shell] enabled = true`; one session per thread
- **`--output-schema`** — validated JSON final output on `agent run`
- **Memories v1** — `agent memories list|add|delete|search`; opt-in `[memories] enabled`
- **`agent runs export --format jsonl-v2`** — normalized replay from run logs

**Docs**
- `README.md` — solo-first quickstart
- `docs/enterprise.md` — Phases 6–18 (serve/OIDC/RBAC/DAG/scheduler)
- `docs/codex-comparison.md` — harness vs official Codex (Phase 22 scorecard)
- `CHANGELOG.md` — v2.1.0 / v2.2.0 / v2.3.0 notes

**Tests:** 920 passed, 1 skipped (Windows AppContainer when `AGENT_TEST_APPCONTAINER≠1`)

**Recent commits (reference):**
- Phase 20: golden-path, repl/tui, openrouter-ux, docs, test-fixes (5 commits)
- Phase 21: checkpoint/resume, pr-description, REPL completion, patch UX, doctor (5 commits)
- Phase 22: review, `--json`, user_input, permissions, plan, hooks, shell, output-schema, memories, release (10+ commits)

### Phase 22 partial (polish candidates, not blockers)

| Item | Status |
|------|--------|
| P3 PTY / unified exec | ⚠️ opt-in; Windows one-shot fallback (doctor reports PTY) |
| P8 Memories | ⚠️ minimal v1; opt-in keyword inject only |
| Long-session compaction | ⚠️ verify task preservation after compact (10-spot check #3) |
| Starlark exec policy | ⚠️ TOML glob allow/deny only |
| OS-native sandbox | ⚠️ heuristic default; kernel modes opt-in |

---

## What I am NOT trying to do

- **Not** rebuilding Codex in Rust
- **Not** wrapping or shelling out to the official `codex` binary
- **Not** expanding enterprise fleet features (serve/OIDC/RBAC/multi-agent DAG/scheduler) unless I explicitly ask
- Enterprise code may exist from Phases 6–18 but is **optional power** — docs live in `docs/enterprise.md`, not the product story

---

## How to use the Codex repo in this workspace

When I ask "how does Codex do X?" or "should we match Y?":

1. **Read** `codex-rs/` (CLI: `codex-rs/cli`, TUI: `codex-rs/tui`, core loop: `codex-rs/core`)
2. **Compare** to equivalent code in `lampcode/agent-cli/agent/`
3. **Recommend** harness parity vs skip — don't blindly port Rust features
4. **Update** `docs/codex-comparison.md` when we add or intentionally skip a Codex behavior

---

## Phase 23 direction (suggested — not started)

Prioritize **daily-driver polish** and closing ⚠️ gaps from Phase 22. Suggested order:

### P0 — harden what Phase 22 shipped
1. **PTY / persistent shell on Windows** — better fallback UX or ConPTY path where feasible
2. **Memories v2** — smarter retrieval/inject (still opt-in; no ML pipeline unless asked)
3. **Compaction regression tests** — long-thread fixture proving task survives compact + resume

### P1 — remaining Codex mechanics (Tier 2 partials)
4. **Exec policy depth** — richer rules without full Starlark unless justified
5. **Kernel sandbox defaults** — clearer doctor guidance; safer solo defaults where possible
6. **Web search quality** — optional provider upgrade path (still OpenRouter-first)

### P2 — polish
7. Review UX — structured findings schema, `--json` CI fixtures
8. JSON stream — event parity audit vs Codex `--json` (field names, ordering)
9. Hook coverage — additional lifecycle events if Codex adds them

### Explicitly skip unless I ask
- ChatGPT OAuth / Plus billing
- Codex Cloud / plugin marketplace
- Multi-agent swarms, distributed scheduler, webhook federation
- Code mode (V8), voice/realtime, remote app-server

Target next release: **v2.4.0** with tests for each feature + full pytest green.

---

## Canonical solo quickstart (must keep working)

```powershell
cd e:\lampcode\agent-cli
pip install -e ".[dev]"
$env:OPENROUTER_API_KEY = "sk-or-v1-..."   # or .env file

cd examples/demo-project
git init   # if needed
agent init --yes
agent run "fix failing tests" --model-profile deep
```

Demo bug: `calc.add(2, 3)` returns `-1` instead of `5`. Agent should patch and pytest should pass.

---

## Working rules for this session

1. **Implement in `lampcode/agent-cli` only** — Codex repo is reference unless I say otherwise
2. **Harness-first** — every change should improve the solo dev loop above
3. **Read before coding:** `README.md`, `docs/codex-comparison.md`, `CHANGELOG.md`, this file, relevant `agent/` modules
4. **Tests required** — mocked where possible; no flaky globals (approval state, DAG threads); run `pytest -q` before claiming done
5. **Logical commits** — one concern per commit, not one giant dump
6. **Docs:** keep README solo-first; enterprise stays in `docs/enterprise.md`
7. **OpenRouter-specific:** maintain cost visibility, model routing, tool-capability warnings — Codex gets these from OpenAI infra; we must engineer them
8. **When unsure:** propose a short plan with Codex reference + gap analysis before large changes
