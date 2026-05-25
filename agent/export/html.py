from __future__ import annotations

import html
from typing import TYPE_CHECKING

from agent.models import Thread

if TYPE_CHECKING:
    from agent.multi_agent.checkpoint import SupervisorCheckpoint


def _last_sync_summary(thread: Thread) -> str | None:
    for turn in reversed(thread.turns):
        for item in reversed(turn.items):
            if item.type == "workspaceSync" and item.status == "completed":
                return f"{item.direction} via {item.transport}: {item.summary}"
    return None


def _render_checkpoint_panel(checkpoint: SupervisorCheckpoint | None) -> str:
    if checkpoint is None or not checkpoint.workers:
        return ""
    rows = []
    for w in checkpoint.workers:
        rows.append(
            f"<tr><td><code>{html.escape(w.worker_id)}</code></td>"
            f"<td>{html.escape(w.status)}</td>"
            f"<td>{html.escape(w.task[:120])}</td></tr>"
        )
    return (
        f'<section class="checkpoint"><h2>Latest checkpoint '
        f'(turn {html.escape(checkpoint.turn_id[:8])}…, {html.escape(checkpoint.status)})</h2>'
        f"<p>Workers: {len(checkpoint.workers)} "
        f"(completed {sum(1 for w in checkpoint.workers if w.status == 'completed')}, "
        f"failed {sum(1 for w in checkpoint.workers if w.status == 'failed')})</p>"
        f"<table><thead><tr><th>ID</th><th>Status</th><th>Task</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def export_thread_html(
    thread: Thread,
    *,
    sandbox: str | None = None,
    backend: str | None = None,
    checkpoint: SupervisorCheckpoint | None = None,
) -> str:
    title = html.escape(thread.display_label())
    meta_rows = [
        f"<li><strong>ID:</strong> <code>{html.escape(thread.id)}</code></li>",
        f"<li><strong>CWD:</strong> <code>{html.escape(thread.cwd)}</code></li>",
        f"<li><strong>Model:</strong> {html.escape(thread.model)}</li>",
    ]
    if sandbox:
        meta_rows.append(f"<li><strong>Sandbox:</strong> {html.escape(sandbox)}</li>")
    if backend:
        meta_rows.append(f"<li><strong>Execution:</strong> {html.escape(backend)}</li>")
    last_sync = _last_sync_summary(thread)
    if last_sync:
        meta_rows.append(f"<li><strong>Last sync:</strong> {html.escape(last_sync)}</li>")

    turns_html: list[str] = []
    for i, turn in enumerate(thread.turns, start=1):
        items_html: list[str] = []
        for item in turn.items:
            if item.type == "userMessage":
                items_html.append(
                    f'<div class="msg user"><strong>User</strong><pre>{html.escape(item.text)}</pre></div>'
                )
            elif item.type == "agentMessage":
                items_html.append(
                    f'<div class="msg assistant"><strong>Assistant</strong><pre>{html.escape(item.text)}</pre></div>'
                )
            elif item.type == "commandExecution":
                backend_note = f" [{item.backend}]" if item.backend else ""
                items_html.append(
                    f'<div class="msg cmd"><strong>Command</strong> ({html.escape(item.status)}){html.escape(backend_note)}'
                    f'<pre>{html.escape(item.command)}</pre></div>'
                )
            elif item.type == "workspaceSync":
                items_html.append(
                    f'<div class="msg sync"><strong>Sync</strong> ({html.escape(item.status)}) '
                    f'{html.escape(item.direction)} via {html.escape(item.transport)}'
                    f'<p>{html.escape(item.summary[:500])}</p></div>'
                )
            elif item.type == "collabWorker":
                items_html.append(
                    f'<div class="msg worker"><strong>Worker</strong> <code>{html.escape(item.worker_id)}</code> '
                    f'({html.escape(item.status)})<pre>{html.escape(item.task)}</pre>'
                    f'{f"<p>{html.escape(item.summary[:500])}</p>" if item.summary else ""}</div>'
                )
            elif item.type == "collabSpawn":
                items_html.append(
                    f'<div class="msg worker"><strong>Worker spawn</strong> ({html.escape(item.status)})'
                    f'<pre>{html.escape(item.task)}</pre></div>'
                )
        turns_html.append(
            f'<section class="turn"><h2>Turn {i} ({html.escape(turn.status)})</h2>'
            + "".join(items_html)
            + "</section>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 960px; }}
.msg {{ border-left: 4px solid #ccc; padding: 0.5rem 1rem; margin: 0.75rem 0; }}
.user {{ border-color: #2563eb; }}
.assistant {{ border-color: #16a34a; }}
.cmd {{ border-color: #ca8a04; }}
.worker {{ border-color: #9333ea; }}
.sync {{ border-color: #0891b2; }}
.checkpoint table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
.checkpoint th, .checkpoint td {{ border: 1px solid #e5e7eb; padding: 0.35rem 0.5rem; text-align: left; }}
pre {{ white-space: pre-wrap; word-break: break-word; }}
code {{ background: #f3f4f6; padding: 0.1rem 0.3rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
<ul>{''.join(meta_rows)}</ul>
{_render_checkpoint_panel(checkpoint)}
{''.join(turns_html)}
</body>
</html>"""


def render_index_html(threads: list[Thread]) -> str:
    rows = []
    for t in threads:
        label = html.escape(t.display_label())
        rows.append(
            f'<li><a href="/threads/{html.escape(t.id)}">{label}</a> '
            f'<small>{html.escape(t.cwd)}</small></li>'
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"/><title>agent-cli threads</title></head>
<body>
<h1>Threads</h1>
<ul>{''.join(rows) if rows else '<li>No threads</li>'}</ul>
</body>
</html>"""
