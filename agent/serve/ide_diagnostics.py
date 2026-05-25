from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.settings import ServeIdeDiagnosticsSettings


@dataclass
class DiagnosticItem:
    line: int
    col: int
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "message": self.message,
        }


def run_diagnostics(
    file_path: Path,
    *,
    settings: ServeIdeDiagnosticsSettings,
    shutil_which: Any = None,
) -> list[dict[str, Any]]:
    if not settings.enabled or not file_path.is_file():
        return []
    ext = file_path.suffix.lower()
    items: list[DiagnosticItem] = []
    if ext == ".py":
        items = _python_diagnostics(file_path, settings, shutil_which=shutil_which)
    elif ext in (".js", ".ts", ".jsx", ".tsx") and settings.js_tool == "eslint":
        items = _eslint_diagnostics(file_path, settings, shutil_which=shutil_which)
    return [i.to_dict() for i in items[: settings.max_diagnostics]]


def _python_diagnostics(
    path: Path,
    settings: ServeIdeDiagnosticsSettings,
    *,
    shutil_which: Any = None,
) -> list[DiagnosticItem]:
    which = shutil_which or __import__("shutil").which
    tool = settings.python_tool
    if tool == "auto":
        tool = "ruff" if which("ruff") else "py_compile"
    if tool == "none":
        return []
    if tool == "ruff":
        return _parse_ruff(path, settings.timeout_sec)
    return _parse_py_compile(path, settings.timeout_sec)


def _parse_py_compile(path: Path, timeout: int) -> list[DiagnosticItem]:
    try:
        proc = subprocess.run(
            ["python", "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [DiagnosticItem(1, 1, "error", str(exc))]
    if proc.returncode == 0:
        return []
    msg = (proc.stderr or proc.stdout or "syntax error").strip()
    line, col = 1, 1
    if "line" in msg:
        import re

        m = re.search(r"line (\d+)", msg)
        if m:
            line = int(m.group(1))
    return [DiagnosticItem(line, col, "error", msg)]


def _parse_ruff(path: Path, timeout: int) -> list[DiagnosticItem]:
    try:
        proc = subprocess.run(
            ["ruff", "check", "--output-format", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [DiagnosticItem(1, 1, "error", str(exc))]
    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return _parse_py_compile(path, timeout)
    items: list[DiagnosticItem] = []
    for entry in data if isinstance(data, list) else []:
        loc = entry.get("location", {})
        items.append(
            DiagnosticItem(
                int(loc.get("row", 1)),
                int(loc.get("column", 1)),
                "error" if str(entry.get("code", "")).startswith("E") else "warning",
                str(entry.get("message", "")),
            )
        )
    return items


def _eslint_diagnostics(
    path: Path,
    settings: ServeIdeDiagnosticsSettings,
    *,
    shutil_which: Any = None,
) -> list[DiagnosticItem]:
    which = shutil_which or __import__("shutil").which
    if not which("eslint"):
        return []
    try:
        proc = subprocess.run(
            ["eslint", "--format", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=settings.timeout_sec,
        )
        data = json.loads(proc.stdout or "[]")
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        return [DiagnosticItem(1, 1, "error", str(exc))]
    items: list[DiagnosticItem] = []
    for file_result in data if isinstance(data, list) else []:
        for msg in file_result.get("messages", []):
            sev = "error" if msg.get("severity", 1) >= 2 else "warning"
            items.append(
                DiagnosticItem(
                    int(msg.get("line", 1)),
                    int(msg.get("column", 1)),
                    sev,
                    str(msg.get("message", "")),
                )
            )
    return items


def parse_ruff_json_output(raw: str) -> list[dict[str, Any]]:
    items: list[DiagnosticItem] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    for entry in data:
        loc = entry.get("location", {})
        items.append(
            DiagnosticItem(
                int(loc.get("row", 1)),
                int(loc.get("column", 1)),
                "error",
                str(entry.get("message", "")),
            )
        )
    return [i.to_dict() for i in items]
