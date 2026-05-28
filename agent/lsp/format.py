from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.lsp.uri import uri_to_path

_SYMBOL_KINDS = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enumMember",
    23: "struct",
    24: "event",
    25: "operator",
    26: "typeParameter",
}


def _kind_name(kind: int | None) -> str:
    if kind is None:
        return "symbol"
    return _SYMBOL_KINDS.get(kind, "symbol")


def _location_line(loc: dict[str, Any]) -> tuple[str, int, int]:
    uri = loc.get("uri", "")
    try:
        path = uri_to_path(uri)
        rel = path.as_posix()
    except Exception:
        rel = uri
    rng = loc.get("range", {})
    start = rng.get("start", {})
    line = int(start.get("line", 0)) + 1
    col = int(start.get("character", 0))
    return rel, line, col


def format_locations(
    items: list[dict[str, Any]] | dict[str, Any] | None,
    *,
    workspace: Path,
    max_items: int = 25,
    max_chars: int = 12_000,
) -> str:
    if not items:
        return "No results."
    if isinstance(items, dict):
        if "uri" in items:
            items = [items]
        else:
            return "No results."

    lines: list[str] = []
    for item in items[:max_items]:
        if "uri" in item and "range" in item:
            rel, line, col = _location_line(item)
            try:
                rel = Path(rel).resolve().relative_to(workspace.resolve()).as_posix()
            except Exception:
                pass
            lines.append(f"  {rel}:{line}:{col}")
        elif "location" in item:
            rel, line, col = _location_line(item["location"])
            try:
                rel = Path(rel).resolve().relative_to(workspace.resolve()).as_posix()
            except Exception:
                pass
            name = item.get("containerName", "")
            prefix = f"{name}." if name else ""
            lines.append(f"  {rel}:{line}:{col}  {prefix}{item.get('name', '')}")
        else:
            lines.append(f"  {item}")

    if len(items) > max_items:
        lines.append(f"\n[... {len(items) - max_items} more omitted ...]")
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 40] + "\n\n[... truncated ...]"
    return text


def format_document_symbols(
    symbols: list[dict[str, Any]] | None,
    *,
    path: str,
    max_items: int = 80,
) -> str:
    if not symbols:
        return f"No symbols in {path}."

    flat: list[tuple[int, str, int, int]] = []

    def walk(nodes: list[dict[str, Any]], prefix: str = "") -> None:
        for node in nodes:
            if len(flat) >= max_items:
                return
            name = node.get("name", "?")
            kind = _kind_name(node.get("kind"))
            rng = node.get("range", {}).get("start", {})
            line = int(rng.get("line", 0)) + 1
            col = int(rng.get("character", 0))
            label = f"{prefix}{name}" if not prefix else f"{prefix}.{name}"
            flat.append((line, kind, col, label))
            children = node.get("children") or []
            if children:
                walk(children, label)

    walk(symbols)
    lines = [f"Symbols in {path} ({len(flat)} shown):", ""]
    for line, kind, col, label in flat:
        lines.append(f"  L{line}:{col}  [{kind}]  {label}")
    return "\n".join(lines)


def format_hover(hover: dict[str, Any] | str | None) -> str:
    if not hover:
        return "No hover information."
    if isinstance(hover, str):
        return hover
    contents = hover.get("contents")
    if isinstance(contents, str):
        return contents
    if isinstance(contents, dict):
        return contents.get("value", str(contents))
    if isinstance(contents, list):
        parts = []
        for block in contents:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("value", str(block)))
        return "\n".join(parts)
    return str(hover)


def format_workspace_symbols(
    symbols: list[dict[str, Any]] | None,
    *,
    workspace: Path,
    max_items: int = 40,
) -> str:
    if not symbols:
        return "No workspace symbols found."
    lines = ["Workspace symbols:", ""]
    for sym in symbols[:max_items]:
        loc = sym.get("location", {})
        rel, line, col = _location_line(loc)
        try:
            rel = Path(rel).resolve().relative_to(workspace.resolve()).as_posix()
        except Exception:
            pass
        kind = _kind_name(sym.get("kind"))
        lines.append(f"  {rel}:{line}:{col}  [{kind}]  {sym.get('name', '')}")
    if len(symbols) > max_items:
        lines.append(f"\n[... {len(symbols) - max_items} more ...]")
    return "\n".join(lines)


def format_workspace_edit(edit: dict[str, Any] | None, *, workspace: Path) -> str:
    if not edit:
        return "Rename produced no edits."
    changes = edit.get("changes") or {}
    doc_changes = edit.get("documentChanges") or []
    lines = ["Workspace edit (apply with apply_patch per file):", ""]

    if changes:
        for uri, edits in changes.items():
            try:
                rel = uri_to_path(uri).resolve().relative_to(workspace.resolve()).as_posix()
            except Exception:
                rel = uri
            lines.append(f"## {rel}")
            for ch in edits[:20]:
                rng = ch.get("range", {})
                start = rng.get("start", {})
                line = int(start.get("line", 0)) + 1
                new_text = ch.get("newText", "").replace("\n", "\\n")[:80]
                lines.append(f"  L{line}: -> {new_text!r}")
            lines.append("")

    for dc in doc_changes[:15]:
        if dc.get("kind") == "create":
            lines.append(f"  create: {dc.get('uri', '')}")
        elif "edits" in dc:
            uri = dc.get("textDocument", {}).get("uri", "")
            try:
                rel = uri_to_path(uri).resolve().relative_to(workspace.resolve()).as_posix()
            except Exception:
                rel = uri
            lines.append(f"## {rel}")
            for ch in dc.get("edits", [])[:20]:
                rng = ch.get("range", {})
                start = rng.get("start", {})
                line = int(start.get("line", 0)) + 1
                new_text = ch.get("newText", "").replace("\n", "\\n")[:80]
                lines.append(f"  L{line}: -> {new_text!r}")

    return "\n".join(lines)


def format_diagnostics(
    items: list[dict[str, Any]] | None,
    *,
    path: str,
    max_items: int = 30,
) -> str:
    if not items:
        return f"No diagnostics for {path}."
    lines = [f"Diagnostics for {path}:", ""]
    for diag in items[:max_items]:
        rng = diag.get("range", {}).get("start", {})
        line = int(rng.get("line", 0)) + 1
        sev = diag.get("severity", 2)
        sev_label = {1: "error", 2: "warning", 3: "info", 4: "hint"}.get(sev, "diag")
        msg = diag.get("message", "")
        lines.append(f"  L{line} [{sev_label}] {msg}")
    if len(items) > max_items:
        lines.append(f"\n[... {len(items) - max_items} more ...]")
    return "\n".join(lines)
