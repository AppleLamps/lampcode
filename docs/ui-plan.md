# TUI UX plan — Codex parity for the conversation canvas

Living plan for making `agent tui` feel like Codex during an actual coding turn. The **chrome** (home screen, footer, slash commands) is Grok/Codex-inspired; this document covers the **transcript pipeline** — what users stare at while the agent works.

**Reference:** Codex TUI in `codex-rs/tui/` (read-only). Use the [Codex reference map](#codex-reference-map-tui-specific) below as a reading list when porting behaviors.  
**Implementation target:** `lampcode/agent-cli` only.  
**Harness parity** (tools, sandbox, review, MCP) lives in [codex-comparison.md](codex-comparison.md) — do not confuse harness ✅ with TUI polish.

---

## Current state (as of v2.11.0)

### Architecture

The TUI is a **Textual app** with a **typed `TranscriptCell` model** and **incremental sync** — the same shape as Codex's `HistoryCell` pipeline, implemented in Python/Rich rather than Rust/ratatui.

```
AgentEvent (worker thread)
    → AgentEventMessage (post_message, non-blocking)
    → apply_event_to_state() → list[TranscriptCell]
    → TranscriptController.sync() → one Static widget per cell + live stream region
```

| Component | Path | Role |
|-----------|------|------|
| Main app | `agent/tui/app.py` | Thin shell: compose, bindings, shared state |
| Mode controllers | `agent/tui/controllers/` | `HomeController`, `ChatController`, `TurnController` |
| Event bridge | `agent/tui/messages.py` | `AgentEventMessage` — worker must not `call_from_thread` per event |
| View model | `agent/tui/view_model.py` | `AgentEvent` → cells; `thread_transcript_from_store()` for resume |
| Transcript sync | `agent/tui/transcript_controller.py` | Incremental mount/update; `rebuild_all()` on session load / expand |
| Transcript pane | `agent/tui/transcript_pane.py` | Per-cell DOM ids, live assistant stream, scroll-to-end |
| Streaming | `agent/tui/streaming_controller.py`, `table_holdback.py` | Code-fence + markdown table holdback (Codex-style) |
| Cell renderers | `agent/tui/cells/*.py` | Rich markup per cell type |
| Diff formatting | `agent/tui/diff_render.py`, `diff_palette.py`, `patch_preview.py` | Codex-style truecolor/256/16 add-delete backgrounds + hunk syntax |
| Composer draft | `agent/tui/composer_draft.py` | Per-thread draft + `MentionBinding` restore on session reload |
| Markdown | `agent/tui/markdown_render.py` | Assistant message markdown → Rich |
| Status row | `agent/tui/status_row.py` | Spinner + elapsed time above composer |
| Footer / chrome | `agent/tui/footer_state.py`, `context_usage.py`, `composer_chrome.py` | Mode-aware footer, context %, mode badge |
| Composer | `agent/tui/composer.py`, `paste_burst.py` | Multiline input; Windows paste-burst heuristic |
| Overlay | `agent/tui/overlay.py` | Full scrollback (`Ctrl+T`) |
| Theme | `agent/tui/theme.py` | Grok-style dark CSS |
| Slash commands | `agent/tui/slash_commands.py` | `/model`, `/plan`, `/compact`, `/cost`, etc. |
| Turn runner | `agent/tui/runner.py` | Background turn execution |
| CLI diff (reuse) | `agent/output_handler.py` | Patch preview on stderr for `agent run` |

### Transcript cell types (`agent/tui/cells/base.py`)

| Cell | Purpose |
|------|---------|
| `UserMessageCell` | User prompt (styled block) |
| `AssistantMessageCell` | Finalized agent text; `streaming` flag during live region |
| `ToolExecCell` | `run_command`, reads, MCP, web search, workers |
| `PatchCell` | `apply_patch` with file summary + colored diff |
| `ToolGroupCell` | Batched parallel `read_file` tools |
| `PlanCell` | `plan.proposed` / `planProposal` on resume |
| `CompactionCell` | `compaction` / `compaction.completed` / store compaction items |
| `ApprovalCell` | Pending approval in transcript |
| `ErrorCell` | Failures, sandbox blocks |
| `TurnSummaryCell` | End-of-turn model, cost, files/lines/commands stats |
| `ReasoningCell` | Collapsible reasoning stream (`agent.reasoning`) |
| `WorkingCell` | Ephemeral in-transcript step indicator |
| `SystemCell` | SSH pool, checkpoints, workspace sync, etc. |

Rendering is centralized in `agent/tui/cells/__init__.py` (`render_cell`).

### What happens during a turn

| Moment | agent-cli TUI |
|--------|----------------|
| **User sends** | `UserMessageCell`; `TranscriptController.sync()` appends widget |
| **Agent streams** | `agent.delta` → `AssistantStreamController` holdback → live stream in pane; flush to `AssistantMessageCell` at tool boundaries / turn end |
| **Tools start** | `tool.pending` → `PatchCell` or `ToolExecCell` (running); parallel reads may merge into `ToolGroupCell` |
| **Tools finish** | `tool.completed` updates status, output, `diff_preview` on patches |
| **Working** | `#status_row` spinner + elapsed; optional `WorkingCell` between steps |
| **Approvals** | `ApprovalCell` + composer footer banner (`render_approval_banner_text`); keys `y` / `n` / `a` / `A` |
| **Plan / compact** | `PlanCell`, `CompactionCell` from live events |
| **Turn end** | `TurnSummaryCell` with cost and diff stats |
| **Resume from store** | `thread_transcript_from_store()` loads `userMessage`, `agentMessage`, `commandExecution`, `fileChange`, `planProposal`, `contextCompaction`, `mcpToolCall`, `webSearch`, `collabWorker`, `workspaceSync` + per-turn summary |

### Chrome and ergonomics (shipped)

- Grok-style centered home screen; session resume picker (`filter_session_threads`)
- Footer context % and configurable statusline items ([context.md](context.md))
- Non-blocking event delivery + turn worker watchdog (clears zombie “Working…”)
- Human-readable [action-log.md](action-log.md) for freeze diagnosis
- `Ctrl+T` transcript overlay; `e` / click to expand long tool output and diffs
- `@file` / `@skill` mention popup (Tab / ↑ / ↓); drafts in `.agent-cli/composer-drafts/`
- Golden string tests for core cells (`tests/golden/tui/*.txt`, `tests/test_tui_cells.py`) — includes plan, compaction
- Style guide: [tui-styles.md](tui-styles.md)

---

## How Codex does it (target behavior)

Codex treats chat as a **typed transcript of `HistoryCell`s**, with buffer-level rendering and terminal scrollback repair on resize.

| Moment | Codex pattern | Key files |
|--------|---------------|-----------|
| **User message** | Dedicated cell with background styling | `tui/src/style.rs` (`user_message_style`) |
| **Agent streaming** | Stable lines + in-flight region; markdown; table/code fence holdback | `tui/src/streaming/controller.rs` |
| **Shell commands** | `ExecCell`: header, live spinner, truncated output | `tui/src/history_cell/` (exec) |
| **Patches** | Per-file summary, unified diff, line numbers, **syntax highlight** | `tui/src/history_cell/patches.rs`, `tui/src/diff_render.rs` |
| **While working** | Status row: `Working (3s • esc to interrupt)` | `tui/src/status_indicator_widget.rs` |
| **Footer** | Model, mode, context %, shortcuts | `tui/src/bottom_pane/footer.rs` |
| **Scrollback** | Overlay + **terminal reflow** on width change | `tui/src/transcript_reflow.rs` |
| **Approvals** | Bottom-pane overlays, not only transcript text | `tui/src/bottom_pane/approval_overlay.rs` |
| **Composer** | Mentions v2, skill/file popups, documented state machine | `tui/src/bottom_pane/chat_composer.rs`, `docs/tui-chat-composer.md` |
| **Visual language** | Green/red/dim/cyan per `styles.md` | `tui/styles.md` |
| **Regression tests** | Hundreds of `insta` buffer snapshots + vt100 suites | `tui/src/history_cell/snapshots/`, `tui/tests/suite/` |

---

## Gap analysis (honest)

| UX principle | Codex | agent-cli TUI |
|--------------|-------|---------------|
| Typed transcript cells | ✅ `HistoryCell` | ✅ `TranscriptCell` + per-type renderers |
| Incremental render (no full clear) | ✅ | ✅ `TranscriptController.sync()` |
| Streaming holdback (fence/table) | ✅ | ✅ `AssistantStreamController` + `table_holdback` |
| Patch/diff readability | ✅ Syntax + theme-aware backgrounds | ✅ Palette + Pygments in-hunk syntax (`terminal_syntax_theme.py`); not full Rust syntect quantization |
| Exec output truncation | ✅ Token/byte policy + “show more” | ✅ `output_truncation.py` middle-trunc + line limits; expand on `e` |
| Working / busy state | ✅ Status widget + motion modes | ✅ `status_row.py` + `WorkingCell` |
| Markdown in replies | ✅ Full pipeline | ✅ Rich `Markdown` + terminal `code_theme`; golden at 80/120 cols |
| Context in footer | ✅ | ✅ `context_usage.py` |
| Approvals discoverable | ✅ Dedicated overlay | ✅ `ApprovalOverlayScreen` + composer `read_only` during modals |
| Plan / compaction cells | ✅ | ✅ Live + resume from store |
| Resume fidelity | ✅ | ✅ Broad store loader (see table above) |
| Transcript overlay | ✅ | ✅ `Ctrl+T` |
| Resize / reflow | ✅ Rebuilds terminal scrollback from cells | ⚠️ Widget resync on resize; **no Codex-style scrollback repair** |
| Composer `@` mentions | ✅ Popups + bindings | ✅ Popup + Tab/↑/↓ + draft bindings on reload (`.agent-cli/composer-drafts/`) |
| `request_user_input` UI | ✅ Full bottom-pane overlay | ✅ Modal overlay (`user_input_overlay.py`) + handler queue |
| Visual regression tests | ✅ insta @ buffer width | ⚠️ ~27 string goldens in `tests/golden/tui/` + pilot turn flows; Codex has hundreds |
| Frame rate / reduced motion | ✅ 120 FPS cap, shimmer, a11y | ✅ `[tui] reduced_motion` / `AGENT_TUI_REDUCED_MOTION` (static status + footer) |
| Product extras | Voice, multi-agent, rate-limit card | ❌ Out of scope unless prioritized |

**Bottom line:** Structure and daily-turn UX are at Codex parity; remaining gaps are **syntect-level syntax theming**, **terminal scrollback repair on resize**, and **golden/regression breadth** — not missing cell types or composer mention drafts.

**Streaming sync (v2.9.5):** `agent.delta` uses `sync_stream_only()` (live stream widget only); other events use signature-based dirty cell sync in `TranscriptController` + `cell_render_signature.py`.

---

## Open backlog (prioritized)

### P0 — Highest perception impact

#### Diff v2 (syntax + theme-aware hunks) — ✅ palette + adaptive syntax (v2.9.5)

**Today:** `diff_palette.py` (Codex colors) + `terminal_syntax_theme.py` maps dark/light/16-color terminals to `github-dark` / `github-light` / `ansi_*` for Rich Syntax; 16-color diffs skip in-hunk syntax to preserve contrast.

**Still open:** Full Codex syntect per-terminal quantization (Rust-side); we use Pygments via Rich.

**Files:** `agent/tui/diff_palette.py`, `agent/tui/diff_render.py`, `agent/tui/terminal_syntax_theme.py`, `agent/tui/cells/patch.py`.

---

#### Exec output truncation policy — ✅ shipped (v2.9.3)

**Today:** `agent/tui/output_truncation.py` — byte middle-truncation, collapsed/expanded line limits, `Total output lines: N` header in `ToolExecCell`.

---

#### Approval UX v2 — ✅ shipped (v2.9.3+)

**Today:** `ApprovalOverlayScreen` on `approval.requested` (scrollable diff, y/n/a/A keys); inline banner fallback; single-key composer approval when overlay closed; composer `read_only` while approval / user-input / transcript modals are open.

**Files:** `agent/tui/approval_overlay.py`, `app.py`, `composer.py`, `cells/error.py`.

---

### P1 — Terminal and composer polish

#### Resize reflow (Codex `transcript_reflow.rs`) — ✅ shipped (v2.9.3)

**Today:** `transcript_reflow.py` debounces resize (75ms), `rebuild_all()` on width change, reflow after stream if resized mid-turn.

---

#### Composer mentions — ✅ shipped (v2.9.4)

**Today:** `#mention_popup` lists `@file` + `@skill` candidates; Tab/↑/↓/click to insert; `MentionBinding` persisted in `.agent-cli/composer-drafts/{thread_id}.json` and restored on session load/resume; bindings rebuilt from text when missing.

**Files:** `agent/tui/mention_popup.py`, `composer_draft.py`, `composer.py`, `app.py`.

---

#### TUI `request_user_input` overlay — ✅ shipped (v2.9.3)

**Today:** `UserInputOverlayScreen` on `user_input.requested`; `set_user_input_handler` + response queue in `runner.py`.

**Still open:** Notes per option (Codex request_user_input pane). **Done:** `questions[]` batch + overlay progress `(2/3)`.

---

#### Snapshot breadth — ✅ expanded (v2.9.5)

**Today:** `tests/golden/tui/` — cell renders (incl. 80/120 cols), footer modes (approval/working/user-input/reverse-search), composer meta (idle/working/approval), streaming table holdback/complete, grouped reads; `tests/test_tui_turn_flow.py` pilot flows.

**Still open:** Codex-scale hundreds of insta snapshots; scrollback repair without full widget rebuild.

**Files:** `tests/test_tui_cells.py`, `tests/test_tui_goldens_chrome.py`, `tests/test_tui_turn_flow.py`, `tests/golden/tui/`.

---

### P2 — Nice parity / docs

| Item | Notes |
|------|--------|
| `docs/tui-styles.md` | ✅ Shipped — semantic colors for TUI |
| `docs/tui-composer.md` | Paste burst + Enter/newline state machine (mirror Codex `tui-chat-composer.md`) |
| Reasoning cell polish | Collapsed-by-default, shimmer optional |
| MCP/web/read distinct icons | ✅ Code-nav icons in `cells/tool.py` (`◎` `⇢` `⇄` `⤴`) |
| Frame budget | Throttle status row ticks if needed on slow terminals |
| Reduced motion | ✅ `[tui] reduced_motion` or `AGENT_TUI_REDUCED_MOTION=1` — static status row + footer |

### Shipped (do not re-open)

- ~~Flat RichLog~~ → typed cells + incremental controller
- ~~Full-log `clear()` on every event~~
- ~~Missing `tool.completed` / plan / compaction handlers~~
- ~~No patch cells~~ → `PatchCell` + `diff_render`
- ~~No working row~~ → `status_row.py`
- ~~No `fileChange` on resume~~ → in `thread_transcript_from_store`
- ~~No Ctrl+T overlay~~ → `overlay.py`
- ~~No golden tests~~ → `tests/golden/tui/` (extend, don’t restart)

---

## Suggested implementation phases

### Phase UI-1 — Core transcript ✅ (done)

Typed cells, incremental sync, tool/patch/exec cells, working row, event wiring, store loader, basic golden tests.

**Exit criteria:** Patch + shell command readable live and after resume — **met**.

### Phase UI-2 — Polish ✅ (shipped v2.9.3–v2.10.0)

Diff palette + adaptive syntax, approval overlay, exec truncation, `docs/tui-styles.md`, incremental `sync_stream_only()`.

**Remaining:** Full Codex syntect quantization; scrollback repair on resize.

### Phase UI-3 — Power ✅ (mostly shipped v2.9.3–v2.9.5)

Resize debounce + `rebuild_all`, composer mention popups + drafts, `request_user_input` overlay, expanded goldens.

**Remaining:** Codex-scale snapshot suite; optional `docs/tui-composer.md`; terminal scrollback repair.

---

## Quick wins vs big lifts

| Quick (days) | Big (weeks) |
|--------------|-------------|
| More golden files (new cell types, edge widths) | Terminal scrollback repair (`transcript_reflow` parity) |
| `docs/tui-composer.md` state-machine doc | Buffer-level insta tests (if ever move off Textual) |
| Reasoning cell collapsed-by-default | Full Codex syntect per-terminal theme quantization |
| MCP/web tool icon polish | FPS cap / shimmer parity |

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
| Composer state machine (narrative) | `docs/tui-chat-composer.md` (in Codex repo) |
| Output truncation | `codex-rs/utils/output-truncation/` |

---

## agent-cli file map (implementation targets)

| Concern | Path |
|---------|------|
| Main TUI app (shell) | `agent/tui/app.py` |
| Mode controllers | `agent/tui/controllers/home_controller.py`, `chat_controller.py`, `turn_controller.py` |
| Event → state | `agent/tui/view_model.py` |
| Transcript sync | `agent/tui/transcript_controller.py`, `transcript_pane.py` |
| Streaming holdback | `agent/tui/streaming_controller.py`, `table_holdback.py` |
| Cell types | `agent/tui/cells/base.py` |
| Cell render | `agent/tui/cells/*.py` |
| Diff | `agent/tui/diff_render.py`, `diff_palette.py`, `cells/patch.py` |
| Composer draft | `agent/tui/composer_draft.py` |
| Markdown | `agent/tui/markdown_render.py` |
| Status / footer | `agent/tui/status_row.py`, `footer_state.py`, `context_usage.py` |
| Composer | `agent/tui/composer.py`, `composer_chrome.py`, `paste_burst.py` |
| Overlay | `agent/tui/overlay.py` |
| Resize reflow | `agent/tui/transcript_reflow.py` |
| Mentions | `agent/tui/mention_popup.py` (bindings + `composer_draft.py`) |
| Output truncation | `agent/tui/output_truncation.py` |
| Theme | `agent/tui/theme.py` |
| Slash commands | `agent/tui/slash_commands.py` |
| Turn runner | `agent/tui/runner.py` |
| Approval / user input overlays | `agent/tui/approval_overlay.py`, `user_input_overlay.py` |
| Golden tests | `tests/test_tui_cells.py`, `tests/golden/tui/` |
| CLI diff preview (reuse) | `agent/output_handler.py` |
| Event payloads | `agent/events.py` |
| Thread store items | `agent/models.py` |

---

## Out of scope (for this plan)

- ChatGPT OAuth / Plus billing
- Cloud agent UI / desktop wrapper
- Voice / realtime TUI / multi-agent swarms UI
- Matching Codex enterprise features (see [enterprise.md](enterprise.md))

---

## Related docs

- [codex-comparison.md](codex-comparison.md) — harness parity matrix + **TUI-only tier**
- [action-log.md](action-log.md) — debugging worker/UI freezes
- [context.md](context.md) — footer context meter
- [roadmap/README.md](roadmap/README.md) — harness phases 24–28
