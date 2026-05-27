# Context: agent-cli harness project (read this first)

Paste this block at the start of Cursor sessions when working in the **Codex + lampcode** workspace.

## What I'm building

I'm building **agent-cli** — a **Python coding-agent harness** powered by **OpenRouter**, inspired by OpenAI's **Codex CLI** but **not** a fork of it and **not** using the official Codex binary or Rust runtime.

**North-star daily loop:**
> init repo → run agent → investigate with tools → edit files → run tests → summarize

**Design reference:** The `openai/codex` repo in this workspace (especially `codex-rs/`) is for **behavior and UX parity**, not runtime dependency.

**My project:** `lampcode/agent-cli` (`e:\lampcode\agent-cli` on Windows). Treat that as the implementation repo. Do **not** modify `openai/codex` unless I explicitly ask for read-only research.

---

## Current state (v2.10.0 — code navigation + project context)

Phase 28 shipped as **v2.9.0**; TUI polish through **v2.9.4**; harness intelligence as **v2.10.0**. Before doing new work, verify:

```powershell
cd e:\lampcode\agent-cli
pytest -q
git log --oneline -5    # expect v2.10.0 at HEAD
```

**Bottom line:** You're on **v2.10.0**. Built-in `file_outline` / `go_to_definition` / `find_references` / `file_imports`; richer `load_project_context()`; safer `agent init` defaults. Full LSP still optional (MCP). TUI backlog: **[ui-plan.md](ui-plan.md)** Tier B.

### v2.10.0 — Code navigation + project context

- **`tools/code_intel.py`:** `file_outline`, `go_to_definition`, `find_references`, `file_imports`
- **`agent/project_context.py`:** README, manifests, CI, test layout, repo map, skills list
- **Prompts:** `apply_patch` DSL, plan `<proposed_plan>`, `CODE_NAVIGATION_DOCS`; Windows `search_repo`
- **Scaffold:** interactive approvals, memories + web search on, auto `post_patch_test`
- **TUI:** incremental transcript sync, controllers split, syntax themes, goldens — see [ui-plan.md](ui-plan.md)
- **Docs:** [code-navigation.md](code-navigation.md)

### v2.9.4 — TUI diff palette + composer drafts

- **`diff_palette.py`:** Codex truecolor/256/16 add-delete backgrounds; wired through `diff_render.py`
- **Composer drafts:** `.agent-cli/composer-drafts/{thread_id}.json` with `MentionBinding` restore on resume
- **Modal lock:** composer `read_only` during approval / user-input / transcript overlays
- **Docs:** README, `tui-styles.md`, `ui-plan.md`, `codex-comparison.md` updated

### v2.9.2 — TUI launch + polish

- **Default entry:** bare `agent` opens TUI; auto-scaffold on first launch
- **Grok-style home:** centered New / Resume / Quit; dark theme; hide worker forks in picker
- **Slash commands:** `/model`, `/plan`, `/compact`, `/cost`, `/help`, …
- **Context footer:** `Context N% left · M% used`
- **Default model:** `minimax/minimax-m2.7`
- **Docs:** `docs/ui-plan.md` — Codex transcript parity roadmap

### v2.9.1 — OpenRouter API parity

- **Native fallback:** `models` + `route: "fallback"` (default); `native_fallback = false` for client-side chain
- **Reasoning:** `reasoning` / `reasoning_details` captured and preserved across tool rounds
- **Structured output:** `--output-schema` → OpenRouter `response_format` when model supports it
- **Cost:** Prefer `usage.cost` from API; `cost_source` in enrich path
- **Context length:** `context_length` in default `fallback_on`
- **Docs:** `docs/openrouter.md`

### Phase 28 (v2.9.0) — observability and trust

- **Debug replay bundles:** `agent runs export --format bundle`
- **Hooks lifecycle v2:** 8 events, stdout JSON, pre-tool block
- **Memories v3:** suggest queue, accept/reject, inject dry-run
- **Plan mode v2:** `<proposed_plan>`, `plan.proposed` event, REPL `/plan`
- **Doctor JSON:** `agent doctor --json`
- **Compaction tuning:** `auto_mid_turn`, REPL `/compact`

### Phase 27 (v2.8.0)

- **Windows ConPTY shell**, **`agent mcp-server`**, Exa/Tavily web search
- **Exec-policy prefix amendments**, review merge-base diff polish

### Phase 26 (v2.7.0) — just shipped (reference)

- **Model preflight:** `agent doctor --models`; once-per-session non-tool model warning
- **Budget caps:** `--max-cost`, `[budget] max_cost_usd_per_turn`; graceful stop + `budget_exceeded` in summary
- **Review CI:** `schemas/review.v1.json`, `--fail-on` / `--severity-threshold` exit codes
- **JSON stream parity:** `docs/json-events.md`, golden fixture
- **Post-patch test:** `[harness] post_patch_test` after successful `apply_patch`
- **Turn stats:** `threads show --stats`; files/commands/tests in `[done]` and `--json`

### Phase 25 (v2.6.0)

- **Thread picker:** `agent threads pick`, `--resume`, REPL `/resume` and `/fork`
- **`agent apply`:** Re-apply last patch from thread history
- **`--ephemeral`:** Non-persistent runs and REPL mode
- **Turn notifications:** `[notify] command` on turn completion
- **Memories v2:** Scoring, cwd-aware inject, sandbox allowlist for memories path

### Phase 24 (v2.5.0)

- **Unified exec v1:** stdin, output caps, yield_ms, session meta on shell tool
- **Approval cache + sandbox retry:** skip duplicate prompts; one escalation retry after approved denial
- **Compaction v2:** AGENTS-aware summaries, compact hooks, multi-compact warning
- **Parallel read-only tools:** batched `read_file` / `search_repo` / `web_search` / code-nav tools per round

### Phase 23 (v2.4.0)

- **Windows persistent shell:** Pipe-based `cmd.exe` when `[shell] enabled`; completion markers
- **Compaction regression test:** Task marker survives compact + message rebuild
- **Review `--json` CI fixture:** Structured findings for scripted runs
- **Doctor:** Windows reports `persistent-pipes` instead of one-shot-only

### Phase 22 (v2.3.0)

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

**OpenRouter UX (Phase 20 + v2.9.1)**
- Model profiles (`--profile`, `--model-profile`)
- `agent/model_routing.py`, default pricing seed, cost tracking (API cost preferred)
- Native OpenRouter fallback routing; reasoning preservation; structured `response_format`
- Tool-support warnings, doctor OpenRouter probe
- Run ends with: `[done] model=… fallback=… cost≈$… tokens in=… out=…`
- `.env` auto-load for `OPENROUTER_API_KEY`
- Full reference: `docs/openrouter.md`

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
- `docs/openrouter.md` — OpenRouter config, fallbacks, reasoning, structured output
- `docs/enterprise.md` — Phases 6–18 (serve/OIDC/RBAC/DAG/scheduler)
- `docs/codex-comparison.md` — harness vs official Codex (Phase 22 scorecard)
- `CHANGELOG.md` — v2.9.1 / v2.9.0 / … release notes

**Tests:** 1008 passed, 2 skipped (Windows AppContainer when `AGENT_TEST_APPCONTAINER≠1`)

**Recent commits (reference):**
- Phase 20: golden-path, repl/tui, openrouter-ux, docs, test-fixes (5 commits)
- Phase 21: checkpoint/resume, pr-description, REPL completion, patch UX, doctor (5 commits)
- Phase 22: review, `--json`, user_input, permissions, plan, hooks, shell, output-schema, memories, release (10+ commits)

### Phase 22 partial (polish candidates, not blockers)

| Item | Status |
|------|--------|
| P3 PTY / unified exec | ✅ exec v1 — stdin, caps, yield_ms; ConPTY still future |
| P8 Memories | ⚠️ minimal v1; opt-in keyword inject only |
| Long-session compaction | ✅ AGENTS-aware summaries + multi-compact warning (Phase 24) |
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

## Phase 27 direction (complete — v2.8.0)

Terminal and embed parity:

1. Windows ConPTY shell backend ✅
2. `agent mcp-server` stdio tool ✅
3. Exa/Tavily web search providers ✅
4. Exec-policy prefix amendments ✅
5. Review custom prompt + merge-base diff ✅

Target release: **v2.8.0** with tests for each feature + full pytest green. ✅

Full step-by-step plans: **[docs/roadmap/phase-27.md](roadmap/phase-27.md)** (v2.8.0) and **[phase-28.md](roadmap/phase-28.md)** (v2.9.0).

---

## Phase 28 direction (complete — v2.9.0)

Observability and local trust:

1. Debug replay bundle export ✅
2. Hooks lifecycle v2 (8 events) ✅
3. Memories suggest queue + accept/reject ✅
4. Plan mode `<proposed_plan>` parsing ✅
5. Doctor `--json` harness report ✅
6. Compaction `auto_mid_turn` + REPL `/compact` ✅

Target release: **v2.9.0** with tests for each feature + full pytest green. ✅

---

## Phase 29 direction (planned — see roadmap)

Candidates from Phase 28 exit notes:

- **`agent runs import-bundle`** — restore thread from bundle
- **MCP approval elicitation** — headless approval via MCP client
- Further Codex terminal parity as needed

---

## Phase 26 direction (complete — v2.7.0)

OpenRouter differentiation:

1. Model preflight in doctor + run warnings ✅
2. Budget caps per turn ✅
3. Review JSON schema v1 + CI exit codes ✅
4. JSON stream parity audit ✅
5. Post-patch test hook ✅
6. Turn stats ✅

Target release: **v2.7.0** with tests for each feature + full pytest green. ✅

---

## Phase 25 direction (complete — v2.6.0)

Daily UX polish:

### P0 ✅
1. Resume/fork interactive picker
2. `agent apply` + `--ephemeral` runs
3. Turn notifications
4. Memories v2

Target release: **v2.6.0** with tests for each feature + full pytest green. ✅

---

## Phase 24 direction (complete — v2.5.0)

Prioritize **reliability core** before more surface area:

### P0 (Phase 24 / v2.5.0) ✅
1. Unified exec v1 — stdin, output caps, session ids ✅
2. Approval cache + sandbox retry escalation ✅
3. Compaction v2 — AGENTS-aware summaries, compact hooks, multi-compact warning ✅
4. Parallel read-only tools ✅

Target release: **v2.5.0** with tests for each feature + full pytest green. ✅

---

## Phase 23 direction (complete — v2.4.0)

Prioritize **daily-driver polish** and closing ⚠️ gaps from Phase 22. Suggested order:

### P0 — harden what Phase 22 shipped
1. **PTY / persistent shell on Windows** — better fallback UX or ConPTY path where feasible ✅ pipe-persistent shipped
2. **Memories v2** — smarter retrieval/inject (still opt-in; no ML pipeline unless asked)
3. **Compaction regression tests** — long-thread fixture proving task survives compact + resume ✅ shipped

### P1 — remaining Codex mechanics (Tier 2 partials)
4. **Exec policy depth** — richer rules without full Starlark unless justified
5. **Kernel sandbox defaults** — clearer doctor guidance; safer solo defaults where possible
6. **Web search quality** — optional provider upgrade path (still OpenRouter-first)

### P2 — polish
7. Review UX — structured findings schema, `--json` CI fixtures ✅ shipped
8. JSON stream — event parity audit vs Codex `--json` (field names, ordering)
9. Hook coverage — additional lifecycle events if Codex adds them

### Explicitly skip unless I ask
- ChatGPT OAuth / Plus billing
- Codex Cloud / plugin marketplace
- Multi-agent swarms, distributed scheduler, webhook federation
- Code mode (V8), voice/realtime, remote app-server

Target release: **v2.4.0** with tests for each feature + full pytest green. ✅

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
3. **Read before coding:** `README.md`, `docs/codex-comparison.md`, `docs/roadmap/`, `CHANGELOG.md`, this file, relevant `agent/` modules
4. **Tests required** — mocked where possible; no flaky globals (approval state, DAG threads); run `pytest -q` before claiming done
5. **Logical commits** — one concern per commit, not one giant dump
6. **Docs:** keep README solo-first; enterprise stays in `docs/enterprise.md`
7. **OpenRouter-specific:** maintain cost visibility, model routing, tool-capability warnings — Codex gets these from OpenAI infra; we must engineer them
8. **When unsure:** propose a short plan with Codex reference + gap analysis before large changes
