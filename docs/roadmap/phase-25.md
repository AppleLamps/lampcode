# Phase 25 — Daily UX (target v2.6.0)

**Goal:** Polish the interactive solo loop so agent-cli *feels* as smooth as Codex TUI/CLI for resume, apply, notifications, and memories.

**Prerequisite:** Phase 24 complete (v2.5.0).

---

## Step 0 — Baseline

1. `pytest -q` green on v2.5.0.
2. Read Codex: `codex-rs/cli/src/main.rs` (`Resume`, `Fork`, `Apply`), `codex-rs/README.md` (notifications).
3. Skim: `agent/repl.py`, `agent/tui/`, `cli/main.py` thread commands.

---

## Step 1 — Resume / fork interactive picker

**Why:** Codex defaults to a picker when resuming; `--last` is the escape hatch.

### 1.1 Design

- `agent threads pick` or `agent repl --resume` with no id → TUI/list picker:
  - Columns: title, cwd, updated_at, model, cost estimate
  - Filter by cwd git root
- `agent threads fork` same picker when no thread id
- REPL: `/resume` opens picker; `/resume last` unchanged behavior

### 1.2 Implement

| File | Change |
|------|--------|
| `agent/threads_picker.py` | New module: list threads from store, rich/table UI |
| `cli/main.py` | `--resume` without id → picker; `threads pick` subcommand |
| `agent/repl.py` | `/resume`, `/fork` picker integration |
| `agent/tui/app.py` | Optional: resume screen (if low cost) |

### 1.3 Tests

- `tests/test_phase25.py::test_thread_picker_lists_by_cwd` (mock store fixtures)
- `tests/test_phase25.py::test_resume_last_unchanged`

### 1.4 Commit

```
feat(threads): interactive resume and fork picker
```

### 1.5 Done when

- `agent threads pick --cwd .` shows numbered list; selection resumes in REPL test.
- `--resume-last` still works without picker.

---

## Step 2 — `agent apply` + `--ephemeral` runs

**Why:** Codex `codex apply` applies last agent diff; `exec --ephemeral` skips persistence.

### 2.1 `agent apply`

- Read last completed turn's `FileChangeItem` / patch from thread JSONL or run log.
- Apply via existing patch applier (`tools/patch.py`) or `git apply` when unified diff available.
- Flags: `--thread-id`, `--dry-run`, `--last`.

### 2.2 `agent run --ephemeral`

- Do not write thread JSONL to store (or write to temp dir deleted on exit).
- Still allow `--json` events; omit thread_id from summary or use `ephemeral-*` id.
- REPL flag: `/ephemeral on` for session.

### 2.3 Implement

| File | Change |
|------|--------|
| `cli/main.py` | `apply` command; `--ephemeral` on `run` |
| `agent/apply_last.py` | New: extract + apply last patch set |
| `agent/store.py` | Ephemeral mode bypass |
| `agent/loop.py` | Respect ephemeral flag on turn end |

### 2.4 Tests

- `tests/test_phase25.py::test_apply_last_dry_run`
- `tests/test_phase25.py::test_ephemeral_run_no_thread_file`

### 2.5 Commits

```
feat(apply): apply last agent patch to working tree
feat(run): --ephemeral for non-persistent one-shot runs
```

### 2.6 Done when

- Mock thread with one `FileChangeItem` → `agent apply --dry-run` shows diff.
- Ephemeral run leaves no new file under `~/.agent-cli/threads/`.

---

## Step 3 — Turn notifications

**Why:** Codex runs a `notify` script when a turn completes (approvals, done).

### 3.1 Design

- Config: `[notify] command = "..."` with env vars: `AGENT_THREAD_ID`, `AGENT_TURN_ID`, `AGENT_STATUS`, `AGENT_CWD`.
- Fire on: turn completed, turn cancelled, approval needed (optional, default off).
- Windows: document PowerShell toast example; no built-in toast required for v2.6.0.

### 3.2 Implement

| File | Change |
|------|--------|
| `agent/notify.py` | Subprocess runner, timeout, never block loop |
| `agent/settings.py` | `NotifySettings` |
| `agent/loop.py` | Call on turn terminal states |
| `agent/init_scaffold.py` | Commented example in default config |

### 3.3 Tests

- `tests/test_phase25.py::test_notify_fired_on_turn_complete` (mock subprocess)

### 3.4 Commit

```
feat(notify): configurable hook command on turn completion
```

### 3.5 Done when

- Configured echo command receives env vars on completed mocked turn.

---

## Step 4 — Memories v2 (loop-integrated, still opt-in)

**Why:** Codex writable `~/.codex/memories` + tooling beats keyword-only inject.

### 4.1 Design

- Retrieval: score = tag overlap + recency + cwd match (simple, no ML).
- After successful turn (tests mentioned in summary or explicit `--remember`), suggest one memory write (auto in `--auto-approve` + config, else prompt).
- Sandbox: when `[memories] enabled`, allow writes under memories path in workspace-write without extra escalation.

### 4.2 Implement

| File | Change |
|------|--------|
| `agent/memories.py` | Scoring, search, inject top-N |
| `agent/loop.py` | Post-turn memory suggestion hook |
| `agent/sandbox/enforcer.py` | Allow memories path |
| `cli/main.py` | `memories search` improvements |

### 4.3 Tests

- `tests/test_phase25.py::test_memory_scoring_prefers_matching_tags`
- `tests/test_phase25.py::test_memory_inject_in_system_prompt`

### 4.4 Commit

```
feat(memories): v2 scoring, inject, and sandbox allowlist
```

### 4.5 Done when

- `[memories] enabled = true` injects relevant memory into prompt on matching task text.
- Memory file path writable under workspace-write without `request_permissions`.

---

## Step 5 — Docs and release

1. `docs/codex-comparison.md` — resume picker, apply, ephemeral, notify, memories v2.
2. `CHANGELOG.md` + `pyproject.toml` → **2.6.0**.
3. `README.md` — short examples for `agent apply`, `--ephemeral`, notify config.

### Commit

```
chore(release): v2.6.0 Phase 25 daily UX
```

### Phase 25 exit criteria

- [ ] `pytest -q` green (target ≥960 tests)
- [ ] Golden path + demo E2E unchanged
- [ ] Picker, apply, ephemeral documented in README

---

## Deferred

- ConPTY (can start here if Phase 24 exec v1 insufficient)
- `codex mcp-server` equivalent (`agent mcp-server`)
- Native Windows toast notifications
