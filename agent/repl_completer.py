from __future__ import annotations

import re
from pathlib import Path


REPL_COMMANDS = (
    "/quit",
    "/exit",
    "/q",
    "/thread",
    "/cost",
    "/usage",
    "/model",
    "/profile",
    "/model-profile",
    "/modelprofile",
    "/clear",
    "/skills",
    "/compact",
)


def install_repl_completer(cwd: Path) -> bool:
    """Install readline tab completion for /commands and @skill mentions."""
    try:
        import readline
    except ImportError:
        return False

    from agent.skills.discovery import discover_skills

    skill_names = sorted({s.name for s in discover_skills(cwd)})

    def _complete(text: str, state: int) -> str | None:
        line = readline.get_line_buffer()
        options: list[str] = []

        at_match = re.search(r"@([\w-]*)$", line)
        if at_match:
            prefix = at_match.group(1).lower()
            options = [f"@{name}" for name in skill_names if name.lower().startswith(prefix)]
        elif line.startswith("/"):
            options = [c for c in REPL_COMMANDS if c.startswith(line.split()[0])]
            if line.startswith("/skills ") and len(line.split()) >= 2:
                prefix = line.split(maxsplit=1)[1].lower()
                options = [name for name in skill_names if name.lower().startswith(prefix)]
        elif text.startswith("@"):
            prefix = text[1:].lower()
            options = [f"@{name}" for name in skill_names if name.lower().startswith(prefix)]

        if state < len(options):
            return options[state]
        return None

    readline.set_completer(_complete)
    readline.parse_and_bind("tab: complete")
    return True
