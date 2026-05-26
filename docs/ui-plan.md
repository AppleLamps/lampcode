# TUI UX plan — Codex parity for the conversation canvas

Living plan for making `agent tui` feel like Codex during an actual coding turn. The **chrome** (home screen, footer, slash commands) is already Grok/Codex-inspired; this document covers the **transcript pipeline** — what users stare at while the agent works.

**Reference:** Codex TUI in `codex-rs/tui/` (read-only).  
**Implementation target:** `lampcode/agent-cli` only.

---

## Current state (baseline)

### Architecture

The TUI uses a Textual app with a flat `RichLog` transcript, not Codex's typed `HistoryCell` pipeline.

| Component | Path | Role today |
|-----------|------|------------|
| Main app | `agent/tui/app.py` | Textual layout, event loop, `_render_transcript()` |
| View model | `agent/tui/view_model.py` | `AgentEvent` → `TranscriptLine` mapping |
| Theme | `agent/tui/theme.py` | Grok-style dark CSS |
| Slash commands | `agent/tui/slash_commands.py` | `/model`, `/plan`, `/compact`, etc. |
| Context footer | `agent/tui/context_usage.py` | `Context N% left · M% used` |
| CLI output (not TUI) | `agent/output_handler.py` | Patch diff preview on stderr for `agent run` |

### What happens during a turn

| Moment | agent-cli TUI today |
|--------|---------------------|
| **User sends message** | Appends `TranscriptLine(role="user")`; full `_render_transcript()` clears and redraws the entire log |
| **Agent responds** | `agent.delta` events append to `assistant_buffer`; on turn complete, buffer becomes assistant line. Streaming shows raw text — no markdown or syntax |
| **Tools start** | `tool.pending` → dim italic line like `▸ apply_patch: …` |
| **Tools finish** | **`tool.completed` not handled in TUI** (CLI stderr does show diff preview) |
| **Approvals** | Yellow `⚠ summary [y/n/a/A]` in transcript; user types in same input box |
| **Reload from store** | `thread_transcript_from_store()` includes user/agent messages and `commandExecution` / `collabWorker` / `workspaceSync` — **not** `fileChange`, `planProposal`, `mcpToolCall`, etc. |

### What already works (recent polish)

- Grok-style centered home screen (no sidebar clutter)
- Footer context % (Codex-like)
- Slash commands in input (`/model`, `/plan`, `/compact`, `/cost`, `/help`, etc.)
- Session resume picker filtered to real threads (`filter_session_threads`)

---

## How Codex does it (target behavior)

Codex treats chat as a **typed transcript of `HistoryCell`s**, not a flat log.

| Moment | Codex pattern | Key files |
|--------|---------------|-----------|
| **User message** | Dedicated cell with background styling | `tui/src/style.rs` (`user_message_style`) |
| **Agent streaming** | Two-region controller: stable lines + in-flight region; markdown; table/code fence holdback | `tui/src/streaming/controller.rs` |
| **Shell commands** | `ExecCell`: header, live spinner, truncated output | `tui/src/history_cell/` (exec) |
| **Patches** | `PatchHistoryCell`: per-file summary, green/red unified diff, line numbers, syntax highlight | `tui/src/history_cell/patches.rs`, `tui/src/diff_render.rs` |
| **While working** | Status row: `Working (3s • esc to interrupt)` with animated indicator | `tui/src/status_indicator_widget.rs` |
| **Footer** | Model, mode, context %, shortcuts — always visible | `tui/src/bottom_pane/footer.rs` |
| **Scrollback** | Full transcript overlay (`Ctrl+T`), resize reflow | `tui/src/transcript_reflow.rs` |
| **Approvals** | Structured cells / composer state, not just transcript text | `tui/src/bottom_pane/` |
| **Visual language** | Green = additions/success, red = errors/deletions, dim = secondary | `tui/styles.md` |

---

## Gap analysis

| UX principle | Codex | agent-cli TUI |
|--------------|-------|---------------|
| Separate content types (message vs tool vs diff vs status) | ✅ Typed cells | ❌ Mostly plain `TranscriptLine` strings |
| Incremental streaming | ✅ Append stable lines, mutate active cell | ⚠️ Re-render entire log each event |
| Patch/diff readability | ✅ Full diff UI | ❌ Missing (CLI stderr has 8-line preview only) |
| Command output | ✅ Collapsible exec blocks | ❌ One-line summary on reload only |
| Working / busy state | ✅ Spinner + elapsed time | ❌ No visible "agent is thinking" row |
| Markdown / code in replies | ✅ Rendered | ❌ Raw text in RichLog |
| Context budget visible | ✅ Footer % | ✅ Recently added |
| Approvals discoverable | ✅ Prominent | ⚠️ Inline text, easy to miss |
| Plan mode output | ✅ `<proposed_plan>` styled cell | ❌ Not in TUI event handler |
| Compaction feedback | ✅ Dedicated cells | ❌ Not in TUI event handler |
| Visual regression tests | ✅ insta snapshots | ❌ No TUI snapshot coverage |
| Resize / long session | ✅ Reflow | ❌ RichLog reflow is basic |

**Bottom line:** The frame is improving; the conversation canvas inside it is still CLI-grade.

---

## Improvement backlog

### P0 — Must fix for "feels like Codex" during a turn

#### 1. Stop full-log re-renders

**Problem:** `_render_transcript()` calls `log.clear()` on every event — flicker, poor streaming feel, scroll position loss.

**Target:** Append/incremental update per cell; keep scroll pinned to bottom.

**Files:** `agent/tui/app.py`, new cell renderer module.

---

#### 2. Wire missing events in TUI

**Problem:** `apply_event_to_state()` handles `agent.delta`, `turn.*`, `tool.pending`, `approval.pending` — but not completions or lifecycle events.

**Wire at minimum:**

| Event | Action |
|-------|--------|
| `tool.completed` | Finalize tool cell; include `diff_preview` for `apply_patch` |
| `plan.proposed` | Distinct plan cell with summary + expandable body |
| `compaction` / `compaction.completed` | Status line or compact cell |
| `tool.completed` (`run_command`) | Exec block with truncated output |

**Files:** `agent/tui/view_model.py`, `agent/events.py` (reference payloads).

---

#### 3. First-class patch/diff cells

**Problem:** No visual diff in TUI; users can't review what changed without leaving the app.

**Target:** Port the spirit of Codex `PatchHistoryCell` + `diff_render`:

- File list summary (`A path`, `M path`, `D path`)
- Colored `+`/`-` lines (green additions, red deletions)
- Optional line numbers
- Start with simplified rendering (no full syntect required for v1)

**Reuse:** `OutputHandler` already has diff preview logic for `agent run` — extract shared diff formatting.

**Files:** new `agent/tui/cells/patch.py`, `agent/output_handler.py` (shared diff helper).

---

#### 4. Exec / tool cells

**Problem:** Tools appear as one dim line: `▸ run_command (completed): pytest -q`.

**Target:** Structured blocks:

```
• run_command
  └ pytest -q
  └ (output, truncated, expandable)
```

Distinct headers/icons per tool family: `run_command`, `apply_patch`, `read_file`, `mcp__*`.

**Files:** new `agent/tui/cells/exec.py`, `agent/tui/cells/tool.py`.

---

#### 5. "Working…" status row

**Problem:** No visible busy state while `_turn_running` — users don't know if the agent is thinking, calling tools, or stuck.

**Target:** Separate status row (not buried in transcript):

```
Working (3s • Ctrl+C to interrupt)
```

Animated spinner when terminal supports it; static text fallback otherwise.

**Reference:** Codex `status_indicator_widget.rs`.

**Files:** `agent/tui/app.py`, new `agent/tui/status_row.py`.

---

#### 6. Render `fileChange` items from thread store

**Problem:** `thread_transcript_from_store()` skips `fileChange` — patches vanish on resume.

**Target:** Load and render file changes with diff snippets on session reload.

**Files:** `agent/tui/view_model.py` (`thread_transcript_from_store`).

---

### P1 — Polish Codex users expect

#### 7. Markdown rendering for assistant messages

Fenced code blocks, headers, lists. Use Textual markdown widget or lightweight custom renderer.

#### 8. User message styling

Background block or left border so user vs agent is obvious at a glance (Codex `user_message_style`).

#### 9. Better approval UX

Modal or highlighted composer banner:

```
Approve: run_command pytest -q?  [y] yes  [n] no  [a] turn  [A] session
```

Not buried in scrollback. Show patch preview in approval prompt before `y` for `apply_patch`.

#### 10. Live streaming assistant text

Render markdown incrementally where safe; don't wait for turn end to finalize assistant block.

#### 11. Turn boundary markers

Subtle divider or inline `[done]` summary: model, cost, files changed — like Codex end-of-turn feedback.

#### 12. Expand/collapse long tool output

Codex truncates with "show more"; avoid dumping everything or collapsing to one line.

---

### P2 — Parity with Codex power features

#### 13. Transcript overlay (`Ctrl+T`)

Full scrollback pager for long sessions.

#### 14. Resize reflow

Re-wrap transcript on terminal width change (Codex `transcript_reflow.rs`).

#### 15. Visual regression tests

Snapshot tests for TUI cells: user message, patch, exec, approval, streaming. Codex uses `insta`; Textual may need custom capture or golden-string tests.

#### 16. MCP / web search / read_file cells

Distinct icons/headers per tool type.

#### 17. Composer enhancements

`@file` / `@skill` mentions, multiline input, Shift+Enter, mode chip in input border.

#### 18. Reasoning display

If model returns reasoning (already captured in loop), optionally show collapsed "thinking" block.

#### 19. Parallel tool round UX

When multiple read tools run in parallel, show grouped progress — not N identical lines.

#### 20. Error / sandbox cells

Red styled blocks for `sandbox.blocked`, failed tools (Codex red = failures per `styles.md`).

---

## Suggested implementation phases

### Phase UI-1 (P0 core) — ~1 week

1. Introduce typed `TranscriptCell` model (replace flat `TranscriptLine` for new content)
2. Incremental render (no full `clear()` on delta)
3. Working status row
4. Wire `tool.completed` + basic exec/patch cells
5. `fileChange` in store loader

**Exit criteria:** A turn with patch + shell command shows structured cells live and after resume.

### Phase UI-2 (P1 polish) — ~1 week

1. User message styling
2. Approval banner in composer
3. Markdown for assistant messages (code fences minimum)
4. Turn-end summary line

**Exit criteria:** Side-by-side with Codex on a demo turn, transcript is readable without squinting.

### Phase UI-3 (P2 power) — ~2 weeks

1. Transcript overlay
2. Resize reflow
3. Snapshot/golden tests
4. Composer `@` mentions + mode chip

**Exit criteria:** Long session usable; visual regressions caught in CI.

---

## Quick wins vs big lifts

| Quick (days) | Big (weeks) |
|--------------|-------------|
| Wire missing events + `fileChange` in store loader | Full `HistoryCell` architecture |
| Working spinner row | Markdown + syntax-highlighted diffs |
| Incremental RichLog append | Transcript overlay + reflow |
| Patch summary from `diff_preview` event data | Visual regression suite |

---

## Codex reference map (TUI-specific)

| Topic | Codex path |
|-------|------------|
| History cells | `codex-rs/tui/src/history_cell/` |
| Patch / diff render | `codex-rs/tui/src/history_cell/patches.rs`, `codex-rs/tui/src/diff_render.rs` |
| Streaming | `codex-rs/tui/src/streaming/controller.rs` |
| Status / working indicator | `codex-rs/tui/src/status_indicator_widget.rs` |
| User message style | `codex-rs/tui/src/style.rs` |
| Footer / composer | `codex-rs/tui/src/bottom_pane/` |
| Transcript reflow | `codex-rs/tui/src/transcript_reflow.rs` |
| Style guide | `codex-rs/tui/styles.md` |

---

## agent-cli file map (implementation targets)

| Concern | Path |
|---------|------|
| Main TUI app | `agent/tui/app.py` |
| Event → state | `agent/tui/view_model.py` |
| Theme | `agent/tui/theme.py` |
| Slash commands | `agent/tui/slash_commands.py` |
| Context footer | `agent/tui/context_usage.py` |
| Turn runner | `agent/tui/runner.py` |
| CLI diff preview (reuse) | `agent/output_handler.py` |
| Event payloads | `agent/events.py` |
| Thread store items | `agent/models.py`, thread JSONL schema |

---

## Out of scope (for this plan)

- ChatGPT OAuth / Plus billing
- Cloud agent UI
- Desktop app wrapper
- Matching Codex enterprise features (see [enterprise.md](enterprise.md))

---

## Related docs

- [codex-comparison.md](codex-comparison.md) — parity matrix (update when UI items ship)
- [roadmap/README.md](roadmap/README.md) — harness phases 24–28
