from agent.events import AgentEvent, EventEmitter


def test_mcp_server_events() -> None:
    events: list[AgentEvent] = []
    emitter = EventEmitter(events.append)
    emitter.mcp_server_connected("t1", "filesystem")
    emitter.mcp_server_failed("t1", "bad", "err")
    assert events[0].type == "mcp.server.connected"
    assert events[1].type == "mcp.server.failed"


def test_skill_activation_event() -> None:
    events: list[AgentEvent] = []
    emitter = EventEmitter(events.append)
    emitter.skill_activation("t1", "turn1", ["pytest-fix"])
    assert events[0].type == "skill.activation"
    assert events[0].data["skills"] == ["pytest-fix"]


def test_project_rules_event() -> None:
    events: list[AgentEvent] = []
    emitter = EventEmitter(events.append)
    emitter.project_rules_loaded("t1", "/proj/AGENTS.md", 123)
    assert events[0].type == "project.rules.loaded"
    assert events[0].data["char_count"] == 123
