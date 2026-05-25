# Phase 24 — Reliability core (target v2.5.0)

**Goal:** Close the biggest Codex harness gaps that affect real repos under stress: exec sessions, sandbox escalation, compaction quality, and parallel reads.

**Baseline:** v2.4.0 — pipe-persistent shell on Windows, compaction task-marker test, review `--json` fixture.

---

## Step 0 — Baseline and scope lock

1. Run `pytest -q`; record pass count in CHANGELOG when releasing.
2. Read Codex references: `orchestrator.rs`, `unified_exec.rs`, `compact.rs`, `parallel.rs`.
3. Skim agent-cli: `agent/loop.py`, `agent/compaction.py`, `agent/execution/shell_session.py`, `approval/gate.py`, `agent/sandbox/enforcer.py`.
4. **Done when:** You can name the four deliverables below and their test files.

---

## Step 1 — Unified exec v1 (stdin + output caps)

**Why:** Codex treats exec as `exec_command` + `write_stdin` with yield limits. One-shot shell breaks interactive CLIs and long-running processes.

### 1.1 Design

- Extend `run_command` tool schema (or add opt-in `exec_session_id`) with:
  - `session_id` — reuse persistent shell from `ShellSessionManager`
  - `stdin` — optional bytes/lines sent before/with command
  - `max_output_chars` — truncate with clear suffix
  - `yield_ms` — max wait before returning partial output (default 10_000)
- Return meta: `{persistent, backend, session_id, truncated}`.

### 1.2 Implement

| File | Change |
|------|--------|
| `agent/execution/shell_session.py` | Output cap, truncation marker, optional partial return on timeout |
| `tools/shell.py` | Wire new args; pass through to session manager |
| `agent/settings.py` | `[shell] max_output_chars`, `default_yield_ms` |
| `cli/main.py` | Doctor row shows session backend + limits |

### 1.3 Tests

- `tests/test_phase24.py::test_exec_session_stdin_and_output_cap`
- `tests/test_phase24.py::test_exec_truncation_marker_in_output`
- Windows + Unix paths in one test module (platform branches OK).

### 1.4 Commit

```
feat(exec): unified exec v1 with stdin, output caps, and session ids
```

### 1.5 Done when

- `[shell] enabled = true` + two-turn `export VAR=x` / `echo $VAR` works on Windows and Unix.
- Output > cap returns truncated body + meta flag.
- `--json` run emits tool meta with session fields.

---

## Step 2 — Approval cache + sandbox retry escalation

**Why:** Codex approves once, retries in escalated sandbox without re-prompting (`ApprovalStore` + orchestrator).

### 2.1 Design

- Session-scoped approval cache keyed by `(tool_name, normalized_args_hash)`.
- On sandbox denial (not user denial): one automatic retry with escalated mode if user approved `a`/`A` or `request_permissions` granted scope.
- Patch approvals: cache per file path in patch (like Codex multi-file apply_patch keys).

### 2.2 Implement

| File | Change |
|------|--------|
| `approval/gate.py` | `ApprovalCache` dataclass; get/put; integrate with `TurnApprovalState` |
| `agent/session.py` | Session-level cache survives across turns when `session_auto_approve` or explicit session approve |
| `agent/loop.py` | On tool precheck failure with retryable sandbox reason → retry once with escalated flags |
| `agent/sandbox/enforcer.py` | Distinguish `deny_permanent` vs `deny_retryable` |

### 2.3 Tests

- `tests/test_phase24.py::test_approval_cache_skips_second_identical_command`
- `tests/test_phase24.py::test_sandbox_retry_after_session_approve`
- Reset cache in `tests/conftest.py` autouse if needed (mirror approval globals pattern).

### 2.4 Commit

```
feat(approval): session approval cache and sandbox retry escalation
```

### 2.5 Done when

- Approve `git status` once with `A`; second identical call in same session does not prompt.
- Simulated sandbox block → retry succeeds after escalation flag set (mock enforcer).

---

## Step 3 — Compaction v2

**Why:** Codex injects AGENTS.md into compaction, runs pre/post hooks, warns on multi-compact accuracy loss.

### 3.1 Design

- `build_compaction_base_instructions()` equivalent: include project rules + original user task snippet.
- Wire `on_pre_compact` / `on_post_compact` hook events (extend `agent/hooks/runner.py`).
- After 2+ compactions in thread, emit user-visible warning (REPL + `[done]` hint in run).
- Replace history strategy: keep compaction summary as last assistant context before recent turns (audit `build_thread_messages`).

### 3.2 Implement

| File | Change |
|------|--------|
| `agent/compaction.py` | AGENTS.md injection; compaction count; warning text |
| `agent/context.py` | Ensure compaction summary message ordering matches Codex “summary before recent” |
| `agent/hooks/runner.py` | `on_pre_compact`, `on_post_compact` |
| `agent/loop.py` | Emit warning event; call hooks around compact |

### 3.3 Tests

- `tests/test_phase24.py::test_compaction_includes_project_rules_in_summary_prompt` (mock client, assert prompt content)
- `tests/test_phase24.py::test_double_compaction_preserves_task_marker` (extend Phase 23 fixture)
- `tests/test_phase24.py::test_compaction_warning_after_second_compact`

### 3.4 Commit

```
feat(compaction): AGENTS-aware summaries, compact hooks, multi-compact warning
```

### 3.5 Done when

- Mocked compact prompt contains AGENTS.md excerpt when present.
- Two compactions still retain task marker in rebuilt messages.
- Warning appears on third+ turn after two compactions.

---

## Step 4 — Parallel read-only tools

**Why:** Codex parallelizes independent read-only tool calls per round.

### 4.1 Design

- Within one model round, if all pending tool calls are in allowlist `{read_file, search_repo, web_search}` (read-only), dispatch concurrently via `ThreadPoolExecutor` or `asyncio.gather` wrapper.
- Writes, patch, shell, MCP stay sequential.
- Cap parallelism: `config.max_parallel_read_tools = 4`.

### 4.2 Implement

| File | Change |
|------|--------|
| `agent/loop.py` | Batch read-only calls; merge results in original order |
| `agent/config.py` | `max_parallel_read_tools` |
| `agent/settings.py` | `[harness] max_parallel_read_tools` |

### 4.3 Tests

- `tests/test_phase24.py::test_parallel_read_files_preserves_order` (mock dispatch with timing)
- `tests/test_phase24.py::test_patch_and_read_not_parallelized`

### 4.4 Commit

```
feat(tools): parallel dispatch for read-only tools in a round
```

### 4.5 Done when

- Three `read_file` calls in one assistant message run concurrently (mock proves overlap).
- Mixed read + `apply_patch` round stays sequential.

---

## Step 5 — Docs, comparison matrix, release

1. Update `docs/codex-comparison.md` Tier 2 rows: unified exec, orchestration, compaction, parallel.
2. Update `docs/session-context.md` → Phase 24 complete, Phase 25 next.
3. `CHANGELOG.md` + `pyproject.toml` → **2.5.0**.
4. `README.md` — one-line mention of parallel reads + exec caps if user-facing.

### Commit

```
chore(release): v2.5.0 Phase 24 reliability core
```

### Phase 24 exit criteria

- [ ] `pytest -q` green (target ≥940 tests)
- [ ] Golden path still passes: `examples/demo-project` mocked E2E
- [ ] `docs/codex-comparison.md` updated
- [ ] 4–6 logical commits on master

---

## Deferred to Phase 25+ (do not block v2.5.0)

- ConPTY / full interactive PTY on Windows
- Starlark exec policy
- Kernel sandbox as default (keep opt-in)
