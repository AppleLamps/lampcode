"""Grok-inspired TUI chrome: dark minimal layout, logo, version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

AGENT_LOGO = """\
[bold #388bfd]       ╭──────────────────╮[/]
[bold #388bfd]       │[/]  [bold white]agent[/]  [dim]cli[/dim]  [bold #388bfd]│[/]
[bold #388bfd]       ╰──────────────────╯[/]
[dim]          local coding assistant[/dim]"""

GROK_CSS = """
Screen {
    background: #0a0a0a;
    layout: vertical;
}

#header {
    dock: top;
    height: 1;
    padding: 0 2;
    color: #8b949e;
    background: #0a0a0a;
}

#app_shell {
    height: 1fr;
    min-height: 0;
    layout: vertical;
    background: #0a0a0a;
}

#main {
    height: 1fr;
    min-height: 0;
    background: #0a0a0a;
}

#home_panel {
    width: 100%;
    height: 1fr;
    min-height: 0;
    align: center middle;
    background: #0a0a0a;
}

#home_stack {
    width: auto;
    height: auto;
    align: center middle;
    padding: 2 0;
}

#logo {
    width: 100%;
    height: auto;
    text-align: center;
    content-align: center middle;
    padding: 0 0 3 0;
}

#home_menu {
    width: 44;
    height: auto;
    background: #0a0a0a;
    border: none;
    padding: 0 1;
    color: #e6edf3;
}

#home_menu > .option-list--option {
    padding: 0 0;
    height: 1;
}

#home_menu > .option-list--option-highlighted {
    background: #161b22;
    color: #ffffff;
}

#resume_menu {
    width: 72;
    height: auto;
    max-height: 16;
    background: #0a0a0a;
    border: solid #30363d;
    padding: 0 1;
    display: none;
}

#resume_menu > .option-list--option-highlighted {
    background: #161b22;
}

#transcript {
    height: 100%;
    border: none;
    background: #0a0a0a;
    padding: 1 3 2 3;
    display: none;
}

#overlay_transcript {
    height: 1fr;
    border: none;
    background: #0a0a0a;
    padding: 1 3;
}

#transcript_cells,
#overlay_transcript_cells {
    width: 100%;
    height: auto;
}

.transcript-cell {
    width: 100%;
    height: auto;
    padding: 0 0 1 0;
}

.transcript-cell.expandable-cell {
    pointer: pointer;
}

.transcript-cell.expandable-cell:hover {
    background: #161b22;
}

.live-stream {
    width: 100%;
    height: auto;
}

#overlay_header {
    dock: top;
    height: 1;
    padding: 0 2;
    background: #0a0a0a;
}

#status_row {
    height: 1;
    padding: 0 3;
    color: #58a6ff;
    background: #0d1117;
    display: none;
}

#approval_banner {
    height: auto;
    max-height: 6;
    min-height: 1;
    padding: 0 2;
    color: #e3b341;
    background: #1c1400;
    border: solid #484f58;
    display: none;
}

#mode_badge {
    width: 8;
    min-width: 8;
    height: 3;
    content-align: center middle;
    border: solid #30363d;
    border-right: none;
    background: #161b22;
    padding: 0 1;
}

#input {
    width: 1fr;
    height: 3;
    min-height: 3;
    border: solid #30363d;
    background: #0d1117;
    color: #e6edf3;
    padding: 0 1;
}

#input:focus {
    border: solid #58a6ff;
}

#bottom_chrome {
    width: 100%;
    height: auto;
    min-height: 7;
    background: #0d1117;
    border-top: solid #21262d;
    padding: 0;
}

#resume_preview {
    height: 2;
    min-height: 2;
    padding: 0 3;
    color: #8b949e;
    background: #0d1117;
}

#composer_meta {
    height: 1;
    min-height: 1;
    padding: 1 3 0 3;
    color: #8b949e;
    background: #0d1117;
}

#composer_row {
    width: 100%;
    height: 3;
    min-height: 3;
    padding: 0 3;
    background: #0d1117;
}

#composer_footer {
    height: 1;
    min-height: 1;
    padding: 0 3 1 3;
    color: #6e7681;
    background: #0d1117;
}
"""


def app_version() -> str:
    try:
        return version("agent-cli")
    except PackageNotFoundError:
        return "dev"


def home_menu_options(*, has_sessions: bool) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = [
        ("new", "New session"),
    ]
    if has_sessions:
        options.append(("resume", "Resume session"))
    options.append(("quit", "Quit"))
    return options
