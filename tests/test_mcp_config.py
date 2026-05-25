from pathlib import Path

from agent.settings import McpConfig, McpServerConfig, load_mcp_config


def test_load_mcp_config_from_toml(tmp_path: Path, monkeypatch) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        """
[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
enabled = true
require_approval = false

[mcp]
startup_timeout_sec = 15
tool_name_prefix = true
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("agent.settings.default_config_path", lambda: cfg_file)
    mcp = load_mcp_config()
    assert "filesystem" in mcp.servers
    assert mcp.servers["filesystem"].command == "npx"
    assert mcp.servers["filesystem"].require_approval is False
    assert mcp.settings.startup_timeout_sec == 15


def test_project_mcp_overrides_user(tmp_path: Path, monkeypatch) -> None:
    user_cfg = tmp_path / "user.toml"
    user_cfg.write_text(
        """
[mcp_servers.filesystem]
command = "npx"
args = ["old"]
enabled = true
""",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    agent_dir = project / ".agent-cli"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        """
[mcp_servers.filesystem]
command = "npx"
args = ["new"]
enabled = false
""",
        encoding="utf-8",
    )
    mcp = load_mcp_config(user_cfg, project)
    assert mcp.servers["filesystem"].args == ["new"]
    assert mcp.servers["filesystem"].enabled is False
