from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ReviewMode = Literal["uncommitted", "base", "commit"]

MAX_PATCH_BYTES = 200_000


@dataclass
class ReviewContext:
    mode: ReviewMode
    label: str
    diff_stat: str
    diff_patch: str
    status: str
    title: str


@dataclass
class ReviewFinding:
    severity: str
    title: str
    detail: str
    file: str | None = None
    line: int | None = None


@dataclass
class ReviewReport:
    summary: str
    findings: list[ReviewFinding]
    suggested_fixes: list[str]
    test_gaps: list[str]
    raw_markdown: str

    @property
    def severity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return counts


def _run_git(cwd: Path, *args: str) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def collect_review_context(
    cwd: Path,
    *,
    mode: ReviewMode,
    base: str | None = None,
    commit: str | None = None,
) -> ReviewContext:
    cwd = cwd.resolve()
    if mode == "uncommitted":
        _, stat, _ = _run_git(cwd, "diff", "--stat")
        _, patch, _ = _run_git(cwd, "diff")
        _, status, _ = _run_git(cwd, "status", "--porcelain")
        label = "uncommitted changes"
        title = "review: uncommitted"
    elif mode == "base":
        ref = base or "main"
        _, stat, _ = _run_git(cwd, "diff", f"{ref}...HEAD", "--stat")
        _, patch, _ = _run_git(cwd, "diff", f"{ref}...HEAD")
        _, status, _ = _run_git(cwd, "status", "--porcelain")
        label = f"changes vs {ref}"
        title = f"review: vs {ref}"
    else:
        sha = commit or "HEAD"
        _, stat, _ = _run_git(cwd, "show", sha, "--stat")
        _, patch, _ = _run_git(cwd, "show", sha, "--patch")
        status = ""
        label = f"commit {sha[:8]}"
        title = f"review: commit {sha[:8]}"

    if len(patch.encode("utf-8", errors="replace")) > MAX_PATCH_BYTES:
        patch = patch.encode("utf-8")[:MAX_PATCH_BYTES].decode("utf-8", errors="ignore")
        patch += "\n\n[... diff truncated at 200KB ...]"

    return ReviewContext(
        mode=mode,
        label=label,
        diff_stat=stat.strip(),
        diff_patch=patch.strip(),
        status=status.strip(),
        title=title,
    )


REVIEW_SYSTEM_APPEND = """
You are performing a **code review** (read-only). Do not modify files unless explicitly told fixes are allowed.

Structure your final response in markdown with these sections:
## Summary
## Findings
List each finding as: `- [severity] title — detail (file:line if known)` where severity is critical|major|minor|nit.
## Suggested fixes
## Test gaps
"""


def build_review_user_prompt(ctx: ReviewContext) -> str:
    parts = [
        f"Review {ctx.label} in this repository.",
        "",
        "Produce sections: Summary, Findings (critical/major/minor/nit), Suggested fixes, Test gaps.",
        "Use read_file and search_repo to inspect context beyond the diff when helpful.",
        "You may run read-only or test commands (pytest, git diff, etc.) — no writes.",
        "",
    ]
    if ctx.status:
        parts.extend(["### git status", "```", ctx.status, "```", ""])
    if ctx.diff_stat:
        parts.extend(["### diff stat", "```", ctx.diff_stat, "```", ""])
    if ctx.diff_patch:
        parts.extend(["### diff", "```diff", ctx.diff_patch, "```", ""])
    if not ctx.diff_patch and not ctx.diff_stat:
        parts.append("_No diff content — report that the review scope is empty._")
    return "\n".join(parts)


def parse_review_markdown(text: str) -> ReviewReport:
    summary = _section(text, "Summary") or text[:500]
    findings_raw = _section(text, "Findings") or ""
    fixes_raw = _section(text, "Suggested fixes") or _section(text, "Suggested Fixes") or ""
    tests_raw = _section(text, "Test gaps") or _section(text, "Test Gaps") or ""

    findings: list[ReviewFinding] = []
    for line in findings_raw.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        m = re.match(
            r"^-\s*\[(critical|major|minor|nit)\]\s*(.+?)(?:\s*[—\-]\s*(.+))?$",
            line,
            re.I,
        )
        if m:
            findings.append(
                ReviewFinding(
                    severity=m.group(1).lower(),
                    title=m.group(2).strip(),
                    detail=(m.group(3) or "").strip(),
                )
            )
        else:
            findings.append(ReviewFinding(severity="minor", title=line.lstrip("- "), detail=""))

    suggested = [ln.strip().lstrip("- ") for ln in fixes_raw.splitlines() if ln.strip().startswith("-")]
    test_gaps = [ln.strip().lstrip("- ") for ln in tests_raw.splitlines() if ln.strip().startswith("-")]

    return ReviewReport(
        summary=summary.strip(),
        findings=findings,
        suggested_fixes=suggested,
        test_gaps=test_gaps,
        raw_markdown=text,
    )


def _section(text: str, name: str) -> str:
    pattern = rf"##\s*{re.escape(name)}\s*\n(.*?)(?=\n##\s|\Z)"
    m = re.search(pattern, text, re.I | re.S)
    return m.group(1).strip() if m else ""


REVIEW_SCHEMA_VERSION = "v1"

SEVERITY_RANK = {
    "critical": 4,
    "major": 3,
    "minor": 2,
    "nit": 1,
}


def parse_fail_on_severities(raw: str) -> list[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return [p for p in parts if p in SEVERITY_RANK]


def review_exceeds_fail_threshold(report: ReviewReport, fail_on: list[str]) -> bool:
    if not fail_on:
        return False
    threshold = min(SEVERITY_RANK[s] for s in fail_on if s in SEVERITY_RANK)
    for finding in report.findings:
        rank = SEVERITY_RANK.get(finding.severity, 0)
        if rank >= threshold:
            return True
    return False


def review_report_to_json(
    report: ReviewReport,
    *,
    thread_id: str,
    model: str,
    cost: float | None,
    schema_version: str = REVIEW_SCHEMA_VERSION,
) -> str:
    return json.dumps(
        {
            "schema_version": schema_version,
            "summary": report.summary,
            "findings": [
                {
                    "severity": f.severity,
                    "title": f.title,
                    "detail": f.detail,
                    "file": f.file,
                    "line": f.line,
                }
                for f in report.findings
            ],
            "severity_counts": report.severity_counts,
            "suggested_fixes": report.suggested_fixes,
            "test_gaps": report.test_gaps,
            "thread_id": thread_id,
            "model": model,
            "cost": cost,
        },
        indent=2,
    )
