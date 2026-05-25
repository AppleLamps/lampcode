# agent-cli vs official Codex CLI

Living parity matrix for the **OpenRouter harness** — not “match everything Codex ships,” but **match the daily solo loop** while keeping enterprise/cloud extras in [enterprise.md](enterprise.md).

**Current release:** v2.6.0 (Phase 25 daily UX).  
**Daily loop:** `init → run in repo → sandboxed tools → patch → rerun commands → compact when long → resume later`

---

## Tier 1 — Must feel like Codex (daily loop)

These should feel solid in real use. If all pass, you're ~80% of Codex-as-harness.

| Codex capability | Status | agent-cli |
|------------------|--------|-----------|
| `run` / non-interactive loop | ✅ | `agent run` |
| Interactive session | ✅ | `agent repl` + `agent tui` |
| Core tools: shell + patch + read/list | ✅ | `run_command`, `apply_patch`, `read_file`, `search_repo`, `write_file` |
| Approvals (exec/patch) | ✅ | `y` / `n` / `a` (turn) / `A` (session); `--auto-approve` |
| Sandbox modes | ✅ | `read-only` / `workspace-write` / `danger-full-access` (heuristic + optional kernel — see Tier 2) |
| Thread persistence + resume | ✅ | JSONL threads; `--resume-last`, `--resume` picker, REPL `/resume` (Phase 25) |
| Compaction | ✅ | Auto at threshold; compaction items in transcript; `/compact` hint in REPL |
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
| **Orchestration (approval cache + sandbox retry)** | ✅ | Session approval cache; one sandbox escalation retry after approved denial (Phase 24) |
| **Parallel read-only tools** | ✅ | `read_file` / `search_repo` / `web_search` batched per round (Phase 24) |
| **`request_user_input` tool** | ✅ | Structured mid-turn questions; REPL/TTY + `AGENT_INPUT_ANSWERS` (Phase 22) |
| **`request_permissions` (mid-turn escalation)** | ✅ | Approval gate + session flags; auto-deny in read-only review (Phase 22) |
| **Thread fork** | ✅ | `agent threads fork`; `forked_from` in JSONL |
| **Hooks (`hooks.json`)** | ✅ | `on_tool_pending`, `on_turn_completed`, `on_pre_compact`, `on_post_compact` (Phase 24) |
| **Memories (cross-session)** | ⚠️ partial | v2 scoring + cwd inject; opt-in `[memories] enabled`; sandbox allowlist (Phase 25) |
| **`agent apply` (last patch)** | ✅ | `agent apply --dry-run` from thread history (Phase 25) |
| **Ephemeral runs** | ✅ | `agent run --ephemeral`; REPL `/ephemeral on` (Phase 25) |
| **Turn notifications** | ✅ | `[notify] command` on completion (Phase 25) |
| **Plan / collaboration modes** | ✅ | `agent run --plan`, REPL `/plan`; `[plan_mode]` tool filter (Phase 22) |
| **REPL `@skill` tab completion** | ✅ | readline completer when available (Phase 21) |
| **Turn checkpoint / resume polish** | ✅ | `[turn_checkpoint]`; cancel saves messages; `--resume-turn` (Phase 21) |
| **`threads pr-description`** | ✅ | Bullet summary + `--summary-only` (Phase 21) |

### Phase 22 scorecard (P0–P8)

| Priority | Target | Status |
|----------|--------|--------|
| P0 | `agent review --uncommitted` / `--base <branch>` | ✅ |
| P1 | Richer JSONL event parity (`--json` typed stream for CI) | ✅ |
| P2 | `request_user_input` tool | ✅ |
| P3 | PTY / unified exec (persistent shell + stdin) | ✅ exec v1; ConPTY still future |
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
| Model routing / fallbacks | ✅ | `[model_routing]`, `fallback_models`, retries |
| Cost visibility | ✅ | Pricing seed, `[done]` line, `agent threads cost` |
| Tool-capable models | ✅ | Warnings for denylist / cache heuristics |
| Web search | ⚠️ | DuckDuckGo optional — not Responses API quality |
| Reasoning effort | ✅ | `reasoning_effort` in profiles → OpenRouter `reasoning.effort` |
| Structured output schema | ✅ | `--output-schema` on `agent run` (Phase 22) |

---

## What we match (summary table)

| Area | Match level |
|------|-------------|
| Daily solo loop | ✅ Strong |
| Review workflow | ✅ First-class command |
| CI / JSON events | ✅ `--json` + jsonl-v2 export |
| Mid-turn UX tools | ✅ input + permissions |
| Persistent shell | ✅ Opt-in pipes; stdin + output caps (Phase 24) |
| Orchestration | ✅ Approval cache + sandbox retry (Phase 24) |
| Compaction quality | ✅ AGENTS-aware + multi-compact warning (Phase 24) |
| Parallel reads | ✅ Read-only tool batching (Phase 24) |
| Resume picker / apply | ✅ Phase 25 |
| Ephemeral + notify | ✅ Phase 25 |
| Memories | ⚠️ v2 scoring + inject (Phase 25) |
| Enterprise cloud | ❌ By design — see enterprise.md |
