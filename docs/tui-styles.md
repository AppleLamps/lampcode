# TUI visual language

Aligned with Codex `codex-rs/tui/styles.md` — keep the transcript readable in any terminal theme.

## Text roles

| Role | Style |
|------|--------|
| Headers | `bold` |
| Primary | default foreground |
| Secondary | `dim` |
| User hints / status | `cyan` |
| Success / additions | `green` |
| Errors / deletions | `red` |
| Agent brand accent | `#58a6ff` |
| Plan mode | `yellow` |

## Cells

- **User messages:** labeled "You", default text
- **Agent messages:** labeled "Agent", markdown when complete
- **Exec / patch / plan:** bordered panels; collapsed by default; click or `e` to expand
- **Approval:** yellow banner above composer; footer switches to `y` / `n` / `a` / `A` hints
- **Working:** blue status row while a turn runs

## Composer chrome

The bottom panel is a fixed stack inside `#app_shell` (not overlapped by the home menu or transcript):

1. **Status line** (`#composer_meta`) — model, mode, sandbox, approvals, context bar, session, etc. Configure with `[tui] statusline` in config.
2. **Composer row** — `CODE` / `PLAN` badge + multiline input (Enter send, Shift+Enter newline).
3. **Footer** (`#composer_footer`) — shortcuts for the current screen (home vs chat vs approval).

Optional rows above the status line: live status (`#status_row`), resume preview (`#resume_preview`), approval banner (`#approval_banner`).

## Avoid

- Custom RGB colors except panel borders and mode badges
- Full transcript clear on every event (use incremental cell sync)
- Hiding sandbox `danger-full-access` without a red label in the status line
