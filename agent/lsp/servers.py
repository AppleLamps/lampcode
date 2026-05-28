from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LanguageServerSpec:
    language_id: str
    command: list[str]
    name: str


@dataclass
class LspProbeResult:
    available: bool
    server: str = ""
    reason: str = ""


PYTHON_EXTENSIONS = frozenset({".py", ".pyi"})
TS_EXTENSIONS = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})


def language_id_for_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in PYTHON_EXTENSIONS:
        return "python"
    if suffix in TS_EXTENSIONS:
        return "javascript" if suffix in {".js", ".jsx", ".mjs", ".cjs"} else "typescript"
    return None


def probe_python_lsp() -> LspProbeResult:
    if shutil.which("pyright-langserver"):
        return LspProbeResult(available=True, server="pyright")
    return LspProbeResult(
        available=False,
        reason="Install pyright: pip install pyright",
    )


def probe_typescript_lsp() -> LspProbeResult:
    if shutil.which("typescript-language-server"):
        return LspProbeResult(available=True, server="typescript-language-server")
    return LspProbeResult(
        available=False,
        reason="Install: npm i -g typescript-language-server typescript",
    )


def python_server_spec() -> LanguageServerSpec | None:
    if shutil.which("pyright-langserver"):
        return LanguageServerSpec(
            language_id="python",
            command=["pyright-langserver", "--stdio"],
            name="pyright",
        )
    return None


def typescript_server_spec() -> LanguageServerSpec | None:
    if shutil.which("typescript-language-server"):
        return LanguageServerSpec(
            language_id="typescript",
            command=["typescript-language-server", "--stdio"],
            name="typescript-language-server",
        )
    return None


def server_spec_for_path(path: Path) -> LanguageServerSpec | None:
    lang = language_id_for_path(path)
    if lang == "python":
        return python_server_spec()
    if lang in ("typescript", "javascript"):
        return typescript_server_spec()
    return None
