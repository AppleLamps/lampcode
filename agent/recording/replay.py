from __future__ import annotations

from agent.events import AgentEvent


def replay_events_to_lines(events: list[AgentEvent]) -> list[str]:
    lines: list[str] = []
    assistant_buffer = ""

    for event in events:
        etype = event.type
        data = event.data

        if etype == "turn.started":
            lines.append("--- turn started ---")
        elif etype == "agent.delta":
            assistant_buffer += data.get("text", "")
        elif etype == "turn.completed":
            if assistant_buffer:
                lines.append(f"assistant: {assistant_buffer.rstrip()}")
                assistant_buffer = ""
            status = data.get("status", "")
            tokens = data.get("estimated_tokens")
            suffix = f" ({tokens} tokens est.)" if tokens is not None else ""
            lines.append(f"--- turn {status}{suffix} ---")
        elif etype == "tool.pending":
            if assistant_buffer:
                lines.append(f"assistant: {assistant_buffer.rstrip()}")
                assistant_buffer = ""
            name = data.get("tool_name", "")
            args = data.get("arguments", {})
            brief = args.get("cmd") or args.get("query") or args.get("path") or str(args)[:80]
            lines.append(f"tool: {name} {brief}")
        elif etype == "approval.requested":
            lines.append(
                f"[approval pending] {data.get('summary', data.get('tool_name', ''))} "
                "[y/n/a/A]"
            )
        elif etype == "item.completed":
            lines.append(
                f"  item {data.get('item_type')} → {data.get('status')}"
            )
        elif etype == "sandbox.blocked":
            lines.append(
                f"[sandbox blocked ({data.get('mode')})] {data.get('reason')}"
            )
            if data.get("command"):
                lines.append(f"  command: {data['command']}")
        elif etype == "isolation.applied":
            lines.append(
                f"[isolation] pid={data.get('pid')} cwd={data.get('cwd')} "
                f"stripped_env={data.get('stripped_env_count')}"
            )
        elif etype == "compaction.completed":
            lines.append(
                f"[compaction] removed {data.get('removed_items')} items"
            )
        elif etype == "error":
            lines.append(f"error: {data.get('message', '')}")
        elif etype == "skill.activation":
            lines.append(f"skills: {', '.join(data.get('skills', []))}")

    if assistant_buffer:
        lines.append(f"assistant: {assistant_buffer.rstrip()}")

    return lines


def format_run_human(events: list[AgentEvent]) -> str:
    return "\n".join(replay_events_to_lines(events))
