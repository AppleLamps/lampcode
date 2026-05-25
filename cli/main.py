from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agent.config import Config
from agent.loop import run_turn
from agent.models import Thread, new_id, utc_now_iso
from agent.store import ThreadStore

app = typer.Typer(no_args_is_help=True, help="Codex-inspired coding agent CLI")
threads_app = typer.Typer(help="Manage conversation threads")
app.add_typer(threads_app, name="threads")

console = Console()


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Task for the agent"),
    cwd: Optional[Path] = typer.Option(
        None, "--cwd", help="Project directory (defaults to current directory)"
    ),
    thread_id: Optional[str] = typer.Option(
        None, "--thread-id", help="Resume an existing thread"
    ),
    model: Optional[str] = typer.Option(None, "--model", help="OpenRouter model slug"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip approval prompts for commands and writes"
    ),
    max_rounds: int = typer.Option(25, "--max-rounds", help="Max tool-call rounds"),
) -> None:
    """Run the agent on a task."""
    workdir = cwd or Path.cwd()
    try:
        config = Config.from_env(
            workdir,
            model=model,
            auto_approve=auto_approve,
            max_rounds=max_rounds,
        )
        config.require_api_key()
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    store = ThreadStore()

    if thread_id:
        try:
            thread = store.load_thread(thread_id)
        except FileNotFoundError:
            console.print(f"[red]Error:[/red] Thread not found: {thread_id}")
            raise typer.Exit(1) from None
        if model:
            thread.model = model
            config.model = model
    else:
        thread = Thread(
            id=new_id(),
            cwd=str(workdir.resolve()),
            model=config.model,
            created_at=utc_now_iso(),
            updated_at=utc_now_iso(),
        )
        store.create_thread(thread)

    console.print(f"[dim]Thread:[/dim] {thread.id}")
    console.print(f"[dim]Model:[/dim] {config.model}")
    console.print(f"[dim]CWD:[/dim] {thread.cwd}")
    console.print()

    def on_delta(text: str) -> None:
        console.print(text, end="")

    try:
        turn = run_turn(thread, prompt, config, store, on_text_delta=on_delta)
    except Exception as exc:
        console.print()
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print()
    if turn.status == "completed":
        console.print(f"[green]Done.[/green] Thread: {thread.id}")
    else:
        console.print(f"[yellow]Turn {turn.status}.[/yellow] Thread: {thread.id}")


@threads_app.command("list")
def threads_list() -> None:
    """List saved conversation threads."""
    store = ThreadStore()
    threads = store.list_threads()

    if not threads:
        console.print("No threads found.")
        return

    table = Table(title="Agent Threads")
    table.add_column("ID", style="cyan")
    table.add_column("CWD")
    table.add_column("Updated")
    table.add_column("Last message")

    for thread in threads:
        table.add_row(
            thread.id[:8] + "...",
            thread.cwd,
            thread.updated_at[:19],
            thread.last_user_message_preview(),
        )

    console.print(table)


@threads_app.command("show")
def threads_show(
    thread_id: str = typer.Argument(..., help="Thread ID (full or prefix)"),
) -> None:
    """Show a human-readable transcript."""
    store = ThreadStore()
    thread = _resolve_thread(store, thread_id)

    console.print(f"[bold]Thread[/bold] {thread.id}")
    console.print(f"CWD: {thread.cwd}")
    console.print(f"Model: {thread.model}")
    console.print(f"Created: {thread.created_at}")
    console.print(f"Updated: {thread.updated_at}")
    console.print()

    for i, turn in enumerate(thread.turns, start=1):
        console.print(f"[bold]--- Turn {i} ({turn.status}) ---[/bold]")
        for item in turn.items:
            if item.type == "userMessage":
                console.print(f"[blue]User:[/blue] {item.text}")
            elif item.type == "agentMessage":
                console.print(f"[green]Agent:[/green] {item.text}")
            elif item.type == "commandExecution":
                console.print(
                    f"[yellow]Command[/yellow] ({item.status}): {item.command}"
                )
                if item.output:
                    console.print(item.output[:500])
            elif item.type == "fileChange":
                console.print(
                    f"[yellow]File change[/yellow] ({item.status}): {item.path}"
                )
                if item.summary:
                    console.print(item.summary)
        console.print()


def _resolve_thread(store: ThreadStore, thread_id: str) -> Thread:
    try:
        return store.load_thread(thread_id)
    except FileNotFoundError:
        pass

    matches = [
        t
        for t in store.list_threads()
        if t.id.startswith(thread_id) or t.id == thread_id
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        console.print(f"[red]Error:[/red] Ambiguous thread prefix: {thread_id}")
        raise typer.Exit(1)

    console.print(f"[red]Error:[/red] Thread not found: {thread_id}")
    raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
