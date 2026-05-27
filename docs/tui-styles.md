# TUI style guide (agent-cli)

Semantic colors for the interactive transcript. Matches the spirit of Codex `codex-rs/tui/styles.md`.

## Text roles

| Role | Rich / CSS | Use for |
|------|------------|---------|
| Primary | default `#e6edf3` | Agent prose, tool args |
| Secondary | `[dim]` / `#8b949e` | Hints, metadata, file summaries |
| Headers | `[bold]` | Tool names, section labels |

## Foreground semantics

| Meaning | Color | Examples |
|---------|-------|----------|
| User / input tips | `#388bfd` (blue) | User message bar, focus ring |
| Success / additions | `green` / `#3fb950` | `+` diff lines, exit 0 |
| Errors / deletions | `red` | `-` diff lines, failures, sandbox blocks |
| Status / working | `#58a6ff` (cyan) | Status row, tool headers |
| Warnings / approval | `yellow` / `#e3b341` | Approval banner, denials |
| Agent label | `#3fb950` | "Agent" header |

## Diff lines

- `+` additions: green gutter; truecolor dark bg `#213A2B` (Codex), light `#dafbe1`
- `-` deletions: red gutter; truecolor dark bg `#4A221D` (Codex), light `#ffebe9`
- 256-color and 16-color fallbacks follow `agent/tui/diff_palette.py` (Codex `diff_render.rs` indices)
- In-hunk syntax: `terminal_syntax_theme.py` → `github-dark` / `github-light` (truecolor/256); disabled on 16-color terminals
- `@@` / `+++` / `---`: cyan
- Context: dim or syntax-highlighted body

## Assistant markdown

- Rendered with Rich `Markdown` (`markdown_render.py` → `assistant_markdown_renderable`)
- Code fences use the same terminal-adaptive Pygments theme as patch diffs

## Avoid

- Custom purple/orange palette colors (poor contrast on arbitrary terminal themes)
- Raw ANSI `black` / `white` for body text
- Burying approvals only in scrollback — use `#approval_banner` + footer `APPROVAL` mode

## Composer

- Draft + `@` mention bindings persist per thread under `.agent-cli/composer-drafts/{thread_id}.json`
- Composer is `read_only` while approval, user-input, or transcript (`Ctrl+T`) modal screens are on the stack

## Chrome

- Background stack: `#0d1117` (main), `#161b22` (panels), `#1c1400` (approval)
- Borders: `#30363d` idle, `#58a6ff` focus, `#e3b341` approval-active

When adding cells, extend this table rather than inventing new colors.
