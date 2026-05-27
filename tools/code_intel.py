from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from agent.paths import resolve_path_within_cwd
from tools.search import search_repo

_JS_TS_SUFFIXES = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_GO_SUFFIX = ".go"
_RS_SUFFIX = ".rs"


@dataclass(frozen=True)
class SymbolHit:
    path: str
    line: int
    kind: str
    name: str
    signature: str = ""


def file_outline(cwd: Path, path: str) -> str:
    """AST- or regex-based symbol outline for one file."""
    resolved = resolve_path_within_cwd(cwd, path)
    if not resolved.is_file():
        return f"File not found: {path}"

    text = resolved.read_text(encoding="utf-8", errors="replace")
    suffix = resolved.suffix.lower()

    if suffix == ".py":
        symbols = _python_symbols(text)
    elif suffix in _JS_TS_SUFFIXES:
        symbols = _js_ts_symbols(text)
    elif suffix == _GO_SUFFIX:
        symbols = _go_symbols(text)
    elif suffix == _RS_SUFFIX:
        symbols = _rust_symbols(text)
    else:
        return f"No outline support for {suffix} files. Use search_repo or read_file."

    if not symbols:
        return f"No symbols found in {path}."
    lines = [f"Outline for {path} ({len(symbols)} symbols):", ""]
    for sym in symbols:
        sig = f" — {sym.signature}" if sym.signature else ""
        lines.append(f"  L{sym.line}  {sym.kind}  {sym.name}{sig}")
    return "\n".join(lines)


def go_to_definition(
    cwd: Path,
    symbol: str,
    *,
    path_hint: str | None = None,
    max_results: int = 15,
) -> str:
    """Find where a symbol is defined (AST in hinted file, then repo patterns)."""
    symbol = symbol.strip()
    if not symbol:
        return "symbol is required."

    hits: list[SymbolHit] = []
    if path_hint:
        try:
            resolved = resolve_path_within_cwd(cwd, path_hint)
            if resolved.is_file():
                text = resolved.read_text(encoding="utf-8", errors="replace")
                rel = _relative_posix(cwd, resolved)
                if resolved.suffix.lower() == ".py":
                    for h in _python_symbols(text, name_filter=symbol):
                        if h.kind in ("function", "class", "method", "variable"):
                            hits.append(
                                SymbolHit(
                                    path=rel,
                                    line=h.line,
                                    kind=h.kind,
                                    name=h.name,
                                    signature=h.signature,
                                )
                            )
                else:
                    hits.extend(_regex_definition_hits(text, rel, symbol))
        except (ValueError, OSError):
            pass

    if len(hits) < max_results:
        hits.extend(
            _repo_definition_search(cwd, symbol, max_results=max_results - len(hits))
        )

    # Deduplicate
    seen: set[tuple[str, int, str]] = set()
    unique: list[SymbolHit] = []
    for h in hits:
        key = (h.path, h.line, h.name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
        if len(unique) >= max_results:
            break

    if not unique:
        return (
            f"No definition found for {symbol!r}. "
            "Try file_outline on the likely file or search_repo with a narrower pattern."
        )
    lines = [f"Definitions for {symbol!r}:", ""]
    for h in unique:
        sig = f" — {h.signature}" if h.signature else ""
        lines.append(f"  {h.path}:{h.line}  [{h.kind}]  {h.name}{sig}")
    return "\n".join(lines)


def find_references(
    cwd: Path,
    symbol: str,
    *,
    path: str | None = None,
    glob: str | None = None,
    max_results: int = 80,
    max_output: int = 20_000,
    prefer_ripgrep: bool = True,
) -> str:
    """Find textual references to a symbol (word-boundary regex)."""
    symbol = symbol.strip()
    if not symbol:
        return "symbol is required."
    if not re.match(r"^[\w.$]+$", symbol):
        return "symbol must be a simple identifier (letters, digits, _, ., $)."

    pattern = rf"\b{re.escape(symbol)}\b"
    return search_repo(
        cwd,
        pattern,
        path=path,
        glob=glob,
        max_results=max_results,
        max_output=max_output,
        prefer_ripgrep=prefer_ripgrep,
    )


def file_imports(cwd: Path, path: str, *, max_results: int = 40) -> str:
    """List imports/requires in a file and resolve project-local targets when possible."""
    resolved = resolve_path_within_cwd(cwd, path)
    if not resolved.is_file():
        return f"File not found: {path}"

    text = resolved.read_text(encoding="utf-8", errors="replace")
    suffix = resolved.suffix.lower()
    rel = _relative_posix(cwd, resolved)

    if suffix == ".py":
        raw_imports = _python_imports(text)
        resolved_imports = [
            (stmt, _resolve_python_import(cwd, resolved.parent, stmt)) for stmt in raw_imports
        ]
    elif suffix in _JS_TS_SUFFIXES:
        raw_imports = _js_imports(text)
        resolved_imports = [
            (stmt, _resolve_js_import(cwd, resolved.parent, stmt)) for stmt in raw_imports
        ]
    else:
        return f"No import parsing for {suffix}. Use read_file and search_repo."

    if not resolved_imports:
        return f"No imports found in {rel}."

    lines = [f"Imports in {rel}:", ""]
    for stmt, target in resolved_imports[:max_results]:
        if target:
            lines.append(f"  {stmt}")
            lines.append(f"    -> {target}")
        else:
            lines.append(f"  {stmt}  (external or unresolved)")
    if len(resolved_imports) > max_results:
        lines.append(f"\n[... {len(resolved_imports) - max_results} more imports omitted ...]")
    return "\n".join(lines)


def _relative_posix(cwd: Path, path: Path) -> str:
    return path.resolve().relative_to(cwd.resolve()).as_posix()


def _python_symbols(text: str, *, name_filter: str | None = None) -> list[SymbolHit]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    hits: list[SymbolHit] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if name_filter is None or node.name == name_filter:
                hits.append(
                    SymbolHit(
                        line=node.lineno,
                        kind="function",
                        name=node.name,
                        signature=_py_func_sig(node),
                        path="",
                    )
                )
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if name_filter is None or node.name == name_filter:
                hits.append(
                    SymbolHit(
                        line=node.lineno,
                        kind="function",
                        name=node.name,
                        signature=_py_func_sig(node),
                        path="",
                    )
                )
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if name_filter is None or node.name == name_filter:
                bases = ", ".join(_unparse_base(b) for b in node.bases[:3])
                sig = f"class {node.name}" + (f"({bases})" if bases else "")
                hits.append(
                    SymbolHit(line=node.lineno, kind="class", name=node.name, signature=sig, path="")
                )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if name_filter is None or item.name == name_filter:
                        hits.append(
                            SymbolHit(
                                line=item.lineno,
                                kind="method",
                                name=f"{node.name}.{item.name}",
                                signature=_py_func_sig(item),
                                path="",
                            )
                        )

    Visitor().visit(tree)

    if name_filter is None:
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        hits.append(
                            SymbolHit(
                                line=node.lineno,
                                kind="variable",
                                name=target.id,
                                path="",
                            )
                        )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                hits.append(
                    SymbolHit(
                        line=node.lineno,
                        kind="variable",
                        name=node.target.id,
                        path="",
                    )
                )
    return hits


def _py_func_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [a.arg for a in node.args.args[:5]]
    extra = len(node.args.args) - len(args)
    arg_text = ", ".join(args)
    if extra > 0:
        arg_text += f", +{extra} more"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({arg_text})"


def _unparse_base(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_unparse_base(node.value)}.{node.attr}"
    return "..."


def _js_ts_symbols(text: str) -> list[SymbolHit]:
    patterns = [
        (r"^\s*export\s+async\s+function\s+(\w+)", "function"),
        (r"^\s*export\s+function\s+(\w+)", "function"),
        (r"^\s*async\s+function\s+(\w+)", "function"),
        (r"^\s*function\s+(\w+)", "function"),
        (r"^\s*export\s+class\s+(\w+)", "class"),
        (r"^\s*class\s+(\w+)", "class"),
        (r"^\s*export\s+const\s+(\w+)", "const"),
        (r"^\s*const\s+(\w+)\s*=", "const"),
        (r"^\s*export\s+interface\s+(\w+)", "interface"),
        (r"^\s*interface\s+(\w+)", "interface"),
        (r"^\s*export\s+type\s+(\w+)", "type"),
        (r"^\s*type\s+(\w+)", "type"),
    ]
    hits: list[SymbolHit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern, kind in patterns:
            m = re.match(pattern, line)
            if m:
                hits.append(SymbolHit(line=line_no, kind=kind, name=m.group(1), path=""))
                break
    return hits


def _go_symbols(text: str) -> list[SymbolHit]:
    hits: list[SymbolHit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)\s*\(", line)
        if m:
            hits.append(SymbolHit(line=line_no, kind="function", name=m.group(1), path=""))
            continue
        m = re.match(r"^type\s+(\w+)\s+", line)
        if m:
            hits.append(SymbolHit(line=line_no, kind="type", name=m.group(1), path=""))
    return hits


def _rust_symbols(text: str) -> list[SymbolHit]:
    hits: list[SymbolHit] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"^\s*(?:pub\s+)?fn\s+(\w+)\s*\(", line)
        if m:
            hits.append(SymbolHit(line=line_no, kind="function", name=m.group(1), path=""))
            continue
        m = re.match(r"^\s*(?:pub\s+)?struct\s+(\w+)", line)
        if m:
            hits.append(SymbolHit(line=line_no, kind="struct", name=m.group(1), path=""))
            continue
        m = re.match(r"^\s*(?:pub\s+)?enum\s+(\w+)", line)
        if m:
            hits.append(SymbolHit(line=line_no, kind="enum", name=m.group(1), path=""))
    return hits


def _regex_definition_hits(text: str, rel_path: str, symbol: str) -> list[SymbolHit]:
    hits: list[SymbolHit] = []
    patterns = [
        (rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(symbol)}\s*\(", "function"),
        (rf"^\s*(?:export\s+)?class\s+{re.escape(symbol)}\b", "class"),
        (rf"^\s*(?:export\s+)?const\s+{re.escape(symbol)}\b", "const"),
        (rf"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?{re.escape(symbol)}\s*\(", "function"),
        (rf"^\s*(?:pub\s+)?fn\s+{re.escape(symbol)}\s*\(", "function"),
    ]
    for line_no, line in enumerate(text.splitlines(), start=1):
        for pattern, kind in patterns:
            if re.match(pattern, line):
                hits.append(
                    SymbolHit(path=rel_path, line=line_no, kind=kind, name=symbol)
                )
                break
    return hits


def _repo_definition_search(cwd: Path, symbol: str, *, max_results: int) -> list[SymbolHit]:
    patterns = [
        rf"^\s*def\s+{re.escape(symbol)}\s*\(",
        rf"^\s*async\s+def\s+{re.escape(symbol)}\s*\(",
        rf"^\s*class\s+{re.escape(symbol)}\s*[\(:]",
        rf"^\s*(?:export\s+)?(?:async\s+)?function\s+{re.escape(symbol)}\s*\(",
        rf"^\s*(?:export\s+)?class\s+{re.escape(symbol)}\b",
        rf"^\s*func\s+(?:\(\w+\s+\*?\w+\)\s+)?{re.escape(symbol)}\s*\(",
        rf"^\s*(?:pub\s+)?fn\s+{re.escape(symbol)}\s*\(",
    ]
    combined = "|".join(f"(?:{p})" for p in patterns)
    raw = search_repo(
        cwd,
        combined,
        glob="*.{py,pyi,js,jsx,ts,tsx,go,rs}",
        max_results=max_results * 3,
        max_output=15_000,
    )
    if raw.startswith("No matches") or raw.startswith("Search error"):
        return []

    hits: list[SymbolHit] = []
    for line in raw.splitlines():
        if not line or line.startswith("[..."):
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel, line_s, content = parts[0], parts[1], parts[2]
        try:
            line_no = int(line_s)
        except ValueError:
            continue
        kind = "definition"
        if "class " in content:
            kind = "class"
        elif "def " in content or "fn " in content or "function " in content:
            kind = "function"
        hits.append(SymbolHit(path=rel, line=line_no, kind=kind, name=symbol, signature=content.strip()[:80]))
        if len(hits) >= max_results:
            break
    return hits


def _python_imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    stmts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                stmts.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.name for a in node.names[:5])
            if len(node.names) > 5:
                names += ", ..."
            level = "." * node.level
            stmts.append(f"from {level}{module} import {names}")
    return stmts


def _js_imports(text: str) -> list[str]:
    stmts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("export ") and " from " in stripped:
            stmts.append(stripped[:120])
        elif "require(" in stripped:
            stmts.append(stripped[:120])
    return stmts


def _resolve_python_import(cwd: Path, file_dir: Path, stmt: str) -> str | None:
    m = re.match(r"from\s+(\.+)\s*([\w.]*)\s+import", stmt)
    if m:
        level = len(m.group(1))
        module = m.group(2).strip()
        base = file_dir
        for _ in range(level - 1):
            base = base.parent
        parts = [p for p in module.split(".") if p]
        return _resolve_module_path(cwd, base, parts)

    m = re.match(r"from\s+([\w.]+)\s+import", stmt)
    if m:
        parts = m.group(1).split(".")
        return _resolve_module_path(cwd, cwd, parts)

    m = re.match(r"import\s+([\w.]+)", stmt)
    if m:
        parts = m.group(1).split(".")
        return _resolve_module_path(cwd, cwd, parts)
    return None


def _resolve_module_path(cwd: Path, base: Path, parts: list[str]) -> str | None:
    if not parts:
        return None
    rel_parts = parts
    candidates = [
        base.joinpath(*rel_parts).with_suffix(".py"),
        base.joinpath(*rel_parts) / "__init__.py",
        cwd.joinpath(*rel_parts).with_suffix(".py"),
        cwd.joinpath(*rel_parts) / "__init__.py",
    ]
    for cand in candidates:
        try:
            cand.resolve().relative_to(cwd.resolve())
        except ValueError:
            continue
        if cand.is_file():
            return _relative_posix(cwd, cand)
    return None


def _resolve_js_import(cwd: Path, file_dir: Path, stmt: str) -> str | None:
    m = re.search(r"from\s+['\"]([^'\"]+)['\"]", stmt)
    if not m:
        m = re.search(r"require\(\s*['\"]([^'\"]+)['\"]", stmt)
    if not m:
        return None
    spec = m.group(1)
    if not spec.startswith("."):
        return None
    base = (file_dir / spec).resolve()
    candidates = [
        base,
        base.with_suffix(".ts"),
        base.with_suffix(".tsx"),
        base.with_suffix(".js"),
        base.with_suffix(".jsx"),
        base / "index.ts",
        base / "index.js",
    ]
    for cand in candidates:
        try:
            cand.resolve().relative_to(cwd.resolve())
        except ValueError:
            continue
        if cand.is_file():
            return _relative_posix(cwd, cand)
    return None
