"""Grok-inspired TUI chrome: dark minimal layout, logo, version."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

AGENT_LOGO = """\
[dim]⠀⠀⠀⠀⠀⣀⣤⣤⣀⠀⠀⠀⠀⠀
⠀⠀⣀⣤⣶⣿⣿⣿⣿⣿⣶⣤⣀⠀⠀
⠀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⠀
⣸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⣸⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇
⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿
⠀⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿
⠀⠀⠙⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠋⠀
⠀⠀⠀⠈⠛⢿⣿⣿⣿⣿⡿⠛⠁⠀⠀[/dim]"""

GROK_CSS = """
Screen {
    background: #0a0a0a;
}

#header {
    dock: top;
    height: 1;
    padding: 0 2;
    color: #8b949e;
    background: #0a0a0a;
}

#main {
    height: 1fr;
    background: #0a0a0a;
}

#home_panel {
    width: 100%;
    height: 100%;
    align: center middle;
    content-align: center middle;
    background: #0a0a0a;
}

#logo {
    width: auto;
    height: auto;
    text-align: center;
    padding-bottom: 2;
}

#home_menu {
    width: 52;
    height: auto;
    background: #0a0a0a;
    border: none;
    padding: 0 2;
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
    padding: 1 3;
    display: none;
}

#tip {
    dock: bottom;
    height: 1;
    padding: 0 2 1 2;
    color: #6e7681;
    background: #0a0a0a;
}

#composer_row {
    dock: bottom;
    height: 3;
    padding: 0 2 0 2;
    background: #0a0a0a;
}

#input {
    width: 1fr;
    border: tall #30363d;
    background: #0a0a0a;
    color: #e6edf3;
    padding: 0 1;
}

#input:focus {
    border: tall #484f58;
}

#status_badge {
    width: auto;
    min-width: 24;
    height: 3;
    content-align: right middle;
    padding: 0 1;
    color: #8b949e;
    border: tall #30363d;
    border-left: none;
    background: #0a0a0a;
}

#footer_bar {
    dock: bottom;
    height: 1;
    padding: 0 2;
    color: #484f58;
    background: #0a0a0a;
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
