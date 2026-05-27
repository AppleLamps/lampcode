# agent-cli vs official Codex CLI

Living parity matrix for the **OpenRouter harness** — not “match everything Codex ships,” but **match the daily solo loop** while keeping enterprise/cloud extras in [enterprise.md](enterprise.md).

**Current release:** v2.10.0 (code navigation tools + project context + harness prompts).  
**Daily loop:** `agent` → run in repo → sandboxed tools → patch → rerun commands → compact when long → resume later

**Read this doc in two layers:**

1. **Harness (Tiers 1–3, OpenRouter)** — agent loop, tools, sandbox, CI, review. Strong parity here does **not** mean the interactive transcript matches Codex pixel-for-pixel.
2. **TUI (below)** — what you see in `agent` / `agent tui`. Detail and backlog: [ui-plan.md](ui-plan.md).

---

## Tier 1 — Must feel like Codex (daily loop)

These should feel solid in real use. If all pass, you're ~80% of Codex-as-harness.

| Codex capability | Status | agent-cli |
|------------------|--------|-----------|
| `run` / non-interactive loop | ✅ | `agent run` |
| Interactive session | ✅ | `agent` (default TUI) + `agent repl` + `agent tui` |
| Core tools: shell + patch + read/list | ✅ | `run_command`, `apply_patch`, `read_file`, `search_repo`, `write_file` |
| Code navigation (lightweight) | ✅ | `file_outline`, `go_to_definition`, `find_references`, `file_imports` — not full LSP ([code-navigation.md](code-navigation.md)) |
| Approvals (exec/patch) | ✅ | `y` / `n` / `a` (turn) / `A` (session); `--auto-approve` |
| Sandbox modes | ✅ | `read-only` / `workspace-write` / `danger-full-access` (heuristic + optional kernel — see Tier 2) |
| Thread persistence + resume | ✅ | JSONL threads; `--resume-last`, `--resume` picker, REPL `/resume` (Phase 25) |
| Compaction | ✅ | Auto at threshold; REPL `/compact` force compact; `auto_mid_turn` toggle (Phase 28) |
| Config + profiles | ✅ | `[model_profiles.*]`, `--profile`, `--model-profile`, `[model_routing]` |
| MCP tools | ✅ | `mcp__{server}__{tool}`; `agent mcp list|tools` |
| Skills + AGENTS.md | ✅ | `SKILL.md` auto-select; `agent init`; project rules |
| Git-aware workspace | ✅ | Warns outside git; `--skip-git-check` |
| Doctor / diagnostics | ✅ | `agent doctor`; OpenRouter reachability + key validity (Phase 21) |
| Streaming tool/output lines | ✅ | `OutputHandler` — tool pending/done on stderr, deltas on stdout |
| End-of-run summary | ✅ | `[done] model=… fallback=… cost≈$… tokens in=… out=…` |
| **`agent review`** | ✅ | `--uncommitted` / `--base` / `--commit`; read-only by default (Phase 22) |

**Verify in practice (10-spot check):**

1. Golden path — `examples/demo-project`: init → run → patch → pytest green ✅ (mocked E2E + manual)
2. Resume restores context — thread JSONL + turn checkpoint messages on `--resume-turn` ✅
3. Compaction preserves task — AGENTS-aware summaries; multi-compact warning; task marker survives double compact ✅
4. Patch UX — preview in approval + diff on tool done (Phase 21) ✅
5. Sandbox defaults safe — demo uses `workspace-write`; default in scaffold is sensible ✅
6. Exec policy predictable — TOML allow/deny + `agent exec-policy test` ✅ (not Starlark — see Tier 2)
7. MCP reliable — namespaced, approvable ✅
8. Streaming during tools — not silent ✅
9. Machine-readable runs — `--jsonl-events`, `--json`, run logs ✅
10. **`agent review`** — uncommitted / `--base` ✅ (Phase 22)

---

## Tier 2 — Uniquely Codex (worth chasing; harness gaps)

| Codex capability | Status | Notes / agent-cli |
|------------------|--------|-------------------|
| **Codex `apply_patch` DSL** | ✅ | `*** Begin Patch` format in `tools/patch.py`; preview + stats (Phase 21) |
| **Structured `exec --json` event stream** | ✅ | `agent run --json` normalized JSONL (`agent/json_stream.py`); `--jsonl-events` legacy compat |
| **`codex review` (first-class review)** | ✅ | `agent review --uncommitted` / `--base` / `--commit` (Phase 22) |
| **Exec policy (Starlark rules)** | ⚠️ partial | TOML glob allow/deny + modes (`prompt` / `untrusted` / `never`); `agent exec-policy test` |
| **OS-native sandbox** | ⚠️ partial | Heuristic sandbox default; opt-in kernel (bubblewrap/seatbelt/AppContainer) — not Codex-lightweight-by-default |
| **Unified exec (PTY + stdin)** | ✅ | Opt-in `[shell] enabled`; stdin + output caps + yield_ms (Phase 24); pipe-persistent Windows + Unix |
| **Background / dev servers** | ⚠️ partial | `run_command` + `background: true` detaches locally; Codex also yields from PTY without a separate flag |
| **Orchestration (approval cache + sandbox retry)** | ✅ | Session approval cache; one sandbox escalation retry after approved denial (Phase 24) |
| **Parallel read-only tools** | ✅ | `read_file` / `search_repo` / `web_search` / code-nav tools batched per round (Phase 24 + v2.10) |
| **apply_patch DSL in prompt** | ✅ | System prompt + tool schema document `*** Begin Patch` format (v2.10) |
| **Plan mode prompt tag** | ✅ | `<proposed_plan>` append when `--plan` (v2.10) |
| **Rich project context** | ✅ | README + manifests + CI + repo map via `project_context.py` (v2.10) |
| **Safer init defaults** | ✅ | `interactive` approvals, memories + web search on, auto `post_patch_test` (v2.10) |
| **`request_user_input` tool** | ✅ | Structured mid-turn questions; REPL/TTY + `AGENT_INPUT_ANSWERS` (Phase 22) |
| **`request_permissions` (mid-turn escalation)** | ✅ | Approval gate + session flags; auto-deny in read-only review (Phase 22) |
| **Thread fork** | ✅ | `agent threads fork`; `forked_from` in JSONL |
| **Hooks (`hooks.json`)** | ✅ | 8 events incl. session start, prompt submit, permission, pre-tool block (Phase 28) |
| **Memories (cross-session)** | ✅ | v3 suggest queue + accept/reject; inject dry-run; opt-in `[memories] enabled` |
| **`agent apply` (last patch)** | ✅ | `agent apply --dry-run` from thread history (Phase 25) |
| **Ephemeral runs** | ✅ | `agent run --ephemeral`; REPL `/ephemeral on` (Phase 25) |
| **Turn notifications** | ✅ | `[notify] command` on completion (Phase 25) |
| **Plan / collaboration modes** | ✅ | `agent run --plan`, REPL `/plan`; `<proposed_plan>` in system prompt + `plan.proposed` event (Phase 28, prompt v2.10) |
| **Run replay bundle** | ✅ | `agent runs export --format bundle`; redacted config (Phase 28) |
| **Doctor JSON** | ✅ | `agent doctor --json` harness diagnostics (Phase 28) |
| **REPL `@skill` tab completion** | ✅ | readline completer when available (Phase 21) |
| **Turn checkpoint / resume polish** | ✅ | `[turn_checkpoint]`; cancel saves messages; `--resume-turn` (Phase 21) |
| **`threads pr-description`** | ✅ | Bullet summary + `--summary-only` (Phase 21) |

### Phase 22 scorecard (P0–P8)

| Priority | Target | Status |
|----------|--------|--------|
| P0 | `agent review --uncommitted` / `--base <branch>` | ✅ |
| P1 | Richer JSONL event parity (`--json` typed stream for CI) | ✅ |
| P2 | `request_user_input` tool | ✅ |
| P3 | PTY / unified exec (persistent shell + stdin) | ✅ ConPTY on Windows + exec v2 API (Phase 27) |
| P4 | `--output-schema` / structured final output | ✅ |
| P5 | `request_permissions` mid-turn sandbox escalation | ✅ |
| P6 | Hooks (`hooks.json`) | ✅ |
| P7 | Plan mode (read-only / no-edit turns) | ✅ |
| P8 | Memories (durable cross-session notes) | ⚠️ minimal v1; opt-in, keyword inject only |

---

## Tier 3 — Codex has it; skip for this harness

Don't chase unless you explicitly pivot. Full detail stays out of the solo README.

| Codex feature | Why skip |
|---------------|----------|
| ChatGPT OAuth / Plus billing | OpenRouter API key |
| Codex Cloud / `codex apply <task-id>` | Cloud product |
| Plugin marketplace | Ecosystem play |
| Multi-agent swarms / CSV orchestration | [enterprise.md](enterprise.md) |
| Code mode (in-process V8) | Niche |
| Voice / realtime TUI | Experimental |
| Remote app-server / IDE daemon | Unless building IDE extension |
| Self-update (`codex update`) | `pip install -e` / releases |
| OSS/local LM Studio routing | OpenRouter-first |

Enterprise serve, OIDC, RBAC, scheduler, program sync, cross-thread DAG → [enterprise.md](enterprise.md) only.

---

## OpenRouter-specific (engineer yourself)

Codex on OpenAI infra gets these natively; the harness must replicate them.

| Need | Status | agent-cli |
|------|--------|-----------|
| Model routing / fallbacks | ✅ | Native `route: "fallback"` or client-side chain; `[model_routing]`, `fallback_models` |
| Cost visibility | ✅ | API `usage.cost` when present; pricing seed fallback; `[done]` + `agent threads cost` |
| Tool-capable models | ✅ | Warnings for denylist / cache heuristics |
| Web search | ✅ | DuckDuckGo + Exa/Tavily providers (Phase 27) |
| MCP server (embed) | ✅ | `agent mcp-server` + `agent_run` tool (Phase 27) |
| Exec policy session amend | ✅ | Prefix allow list + `p` approval key (Phase 27) |
| Review merge-base diff | ✅ | `--base` uses merge-base; custom prompt (Phase 27) |
| Model preflight table | ✅ | `agent doctor --models` (Phase 26) |
| Per-turn cost cap | ✅ | `--max-cost`, `[budget] max_cost_usd_per_turn` (Phase 26) |
| Review CI exit codes | ✅ | `--fail-on`, `review.v1.json` schema (Phase 26) |
| Post-patch test hook | ✅ | `[harness] post_patch_test` (Phase 26) |
| Turn / thread stats | ✅ | `threads show --stats`, extended `[done]` line (Phase 26) |
| JSON event catalog | ✅ | `docs/json-events.md` + golden fixture (Phase 26) |
| Reasoning effort | ✅ | `reasoning_effort` in profiles → `reasoning.effort`; `reasoning_details` preserved in tool loop (v2.9.1) |
| Structured output schema | ✅ | `--output-schema` → OpenRouter `response_format` when model supports it (v2.9.1) |
| Context-length fallback | ✅ | `context_length` in `fallback_on`; 400 body classification (v2.9.1) |

---

## TUI — Interactive transcript (separate from harness)

Evaluates `agent` / `agent tui` only. Codex reference: `codex-rs/tui/` (read-only). Implementation backlog: [ui-plan.md](ui-plan.md).

### TUI Tier A — Structure and daily turn (shipped)

| Capability | Status | agent-cli |
|------------|--------|-----------|
| Typed transcript (not flat log) | ✅ | `TranscriptCell` + `agent/tui/cells/*` |
| Incremental render (per-cell widgets) | ✅ | `TranscriptController` + `TranscriptPane` |
| Live assistant streaming + holdback | ✅ | `streaming_controller.py`, `table_holdback.py` |
| Tool / patch / exec cells | ✅ | `ToolExecCell`, `PatchCell`, colored diff + file summary |
| Parallel read grouping | ✅ | `ToolGroupCell` |
| Working / busy indicator | ✅ | `status_row.py`, `WorkingCell` |
| Plan + compaction cells | ✅ | `PlanCell`, `CompactionCell`; live events + store reload |
| Turn summary (model, cost, stats) | ✅ | `TurnSummaryCell` |
| Approvals in session | ✅ | `ApprovalCell` + composer banner; `y` / `n` / `a` / `A` |
| Context % in footer | ✅ | `context_usage.py` |
| Transcript overlay | ✅ | `Ctrl+T` → `overlay.py` |
| Resume transcript from thread store | ✅ | `thread_transcript_from_store()` — messages, exec, patches, plan, MCP, web search, workers |
| Non-blocking UI events | ✅ | `AgentEventMessage` / `post_message` (see [action-log.md](action-log.md)) |
| Expand long output / diffs | ✅ | `e` key, click expandable cells |
| Basic visual regression | ✅ | `tests/golden/tui/*.txt` (8 fixtures) |

### TUI Tier B — Polish gaps (worth chasing)

| Capability | Status | Gap vs Codex |
|------------|--------|----------------|
| Patch diff v2 (syntax + theme backgrounds) | ✅ | `diff_palette.py` + `terminal_syntax_theme.py` (github-dark/light, ansi_*); 16-color skips in-hunk syntax |
| Exec output truncation policy | ✅ | `output_truncation.py` — byte middle-trunc + `Total output lines: N` |
| Approval UX (dedicated overlay / locked composer) | ✅ | `ApprovalOverlayScreen` + composer blocked while modals open; inline y/n/a/A when banner only |
| Resize reflow | ⚠️ partial | Debounced `rebuild_all` on width change; Codex also repairs terminal scrollback |
| Composer `@file` / `@skill` popups | ✅ | Mention popup + Tab/↑/↓; draft bindings in `.agent-cli/composer-drafts/` |
| `request_user_input` TUI overlay | ✅ | Modal overlay + `questions[]` batch wizard `(n/N)` |
| Markdown depth | ✅ Rich MD | `markdown_render.py` → Rich `Markdown` + adaptive code themes; goldens at 80/120 cols |
| Snapshot / regression breadth | ⚠️ partial | ~25 string goldens + pilot turn flows; Codex has hundreds of insta snapshots |
| Frame budget / reduced motion | ⚠️ partial | `[tui] reduced_motion` / env disables spinner; no FPS cap yet |
| Rate-limit / account status strip | ❌ | Codex status card (ChatGPT quotas); we show OpenRouter cost/model in footer instead |

### TUI Tier C — Skip unless product pivots

| Codex TUI feature | Why skip |
|-------------------|----------|
| Voice input/output | Experimental; OpenRouter harness |
| Multi-agent / swarms UI | [enterprise.md](enterprise.md) |
| Theme picker / pets / onboarding NUX | Product chrome; Grok theme is intentional |
| Image attachments in user cells | Not required for solo coding loop |
| ChatGPT OAuth / Plus rate limits in footer | OpenRouter API key model |

### TUI summary

| Area | Match level |
|------|-------------|
| Transcript architecture | ✅ Strong (cells + incremental sync) |
| Daily turn readability | ✅ Good (patch/exec/plan/working) |
| Diff / exec visual fidelity | ⚠️ Behind Codex |
| Composer / mid-turn structured UI | ⚠️ Behind Codex |
| Terminal engineering (reflow, snapshots) | ⚠️ Behind Codex |
| OpenRouter footer (model, cost, routing) | ✅ Differentiator vs Codex |

---

## What we match (summary table)

| Area | Match level |
|------|-------------|
| Daily solo loop | ✅ Strong |
| Review workflow | ✅ First-class command |
| CI / JSON events | ✅ `--json` + jsonl-v2 export |
| Mid-turn UX tools | ✅ input + permissions |
| Persistent shell | ✅ ConPTY (Windows) + pipes/pty; stdin API (Phase 27) |
| MCP embed | ✅ `agent mcp-server` (Phase 27) |
| Web search providers | ✅ Exa/Tavily + DuckDuckGo (Phase 27) |
| Exec policy amend | ✅ Prefix allow list (Phase 27) |
| Review merge-base | ✅ Custom prompt + merge-base diff (Phase 27) |
| Orchestration | ✅ Approval cache + sandbox retry (Phase 24) |
| Compaction quality | ✅ AGENTS-aware + multi-compact warning (Phase 24) |
| Parallel reads | ✅ Read-only tool batching (Phase 24) |
| Resume picker / apply | ✅ Phase 25 |
| Ephemeral + notify | ✅ Phase 25 |
| Memories | ⚠️ v2 scoring + inject (Phase 25) |
| Budget caps / review CI | ✅ Phase 26 |
| Turn stats + post-patch hook | ✅ Phase 26 |
| Enterprise cloud | ❌ By design — see enterprise.md |
| **TUI transcript (structure)** | ✅ Strong | See [TUI Tier A](#tui-tier-a--structure-and-daily-turn-shipped) |
| **TUI transcript (polish)** | ⚠️ Partial | Syntect-level diff syntax, scrollback repair, golden breadth — [TUI Tier B](#tui-tier-b--polish-gaps-worth-chasing), [ui-plan.md](ui-plan.md) |
