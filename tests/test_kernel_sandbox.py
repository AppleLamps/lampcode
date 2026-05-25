from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.config import Config
from agent.events import EventEmitter
from agent.sandbox.kernel import (
    KernelSandboxSettings,
    apply_kernel_sandbox,
    probe_capabilities,
)
from agent.sandbox.kernel.factory import select_kernel_backend
from agent.sandbox.kernel.linux_bubblewrap import LinuxBubblewrapBackend
from agent.sandbox.kernel.policy import kernel_enabled_for_mode
from agent.sandbox.kernel.windows_restricted import WindowsRestrictedBackend
from agent.sandbox.policy import SandboxMode


def _cfg(**kw) -> Config:
    c = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    c.sandbox_mode = SandboxMode.WORKSPACE_WRITE
    c.sandbox_kernel = KernelSandboxSettings(enabled=True, **kw)
    c.isolation.enabled = False
    return c


def test_kernel_disabled_by_default_mode_danger() -> None:
    c = _cfg()
    c.sandbox_mode = SandboxMode.DANGER_FULL_ACCESS
    r = apply_kernel_sandbox(c, cmd="echo", cwd=".")
    assert r.applied is False


def test_kernel_enabled_for_workspace_write() -> None:
    assert kernel_enabled_for_mode(SandboxMode.WORKSPACE_WRITE, kernel_enabled=True)
    assert not kernel_enabled_for_mode(SandboxMode.DANGER_FULL_ACCESS, kernel_enabled=True)


def test_select_auto_windows() -> None:
    s = KernelSandboxSettings(backend="auto")
    with patch.object(sys, "platform", "win32"):
        b = select_kernel_backend(s)
    assert isinstance(b, WindowsRestrictedBackend)


def test_select_bubblewrap() -> None:
    s = KernelSandboxSettings(backend="bubblewrap")
    b = select_kernel_backend(s)
    assert b is not None
    assert b.name == "bubblewrap"


def test_bwrap_argv_read_only() -> None:
    s = KernelSandboxSettings(
        linux=__import__("agent.sandbox.kernel", fromlist=["KernelLinuxSettings"]).KernelLinuxSettings(
            ro_bind_paths=["/usr"]
        )
    )
    b = LinuxBubblewrapBackend(s)
    with patch.object(b, "available", return_value=True):
        r = b.wrap_command(cmd="echo hi", cwd="/tmp/ws", sandbox_mode="read-only", settings=s)
    assert r.argv is not None
    assert "bwrap" in r.argv[0] or r.argv[0] == "bwrap"
    assert "--ro-bind" in r.argv


def test_bwrap_argv_workspace_write() -> None:
    s = KernelSandboxSettings()
    b = LinuxBubblewrapBackend(s)
    r = b.wrap_command(cmd="echo", cwd="/tmp/ws", sandbox_mode="workspace-write", settings=s)
    assert "--bind" in r.argv


def test_apply_kernel_emits_applied_event() -> None:
    events: list[str] = []
    emitter = EventEmitter(lambda e: events.append(e.type))
    c = _cfg(backend="windows_restricted")
    with patch.object(WindowsRestrictedBackend, "available", return_value=True):
        r = apply_kernel_sandbox(c, cmd="echo", cwd=".", emitter=emitter)
    assert r.applied is True
    assert any("sandbox.kernel.selected" in t for t in events)
    assert any("sandbox.kernel.applied" in t for t in events)


def test_fail_open_unavailable_backend() -> None:
    events: list[str] = []
    emitter = EventEmitter(lambda e: events.append(e.type))
    c = _cfg(backend="bubblewrap", fail_open=True)
    with patch.object(LinuxBubblewrapBackend, "available", return_value=False):
        r = apply_kernel_sandbox(c, cmd="echo", cwd=".", emitter=emitter)
    assert r.applied is False
    assert any("sandbox.kernel.fallback" in t for t in events)


def test_fail_closed_raises() -> None:
    c = _cfg(backend="bubblewrap", fail_open=False)
    with patch.object(LinuxBubblewrapBackend, "available", return_value=False):
        with pytest.raises(RuntimeError):
            apply_kernel_sandbox(c, cmd="echo", cwd=".")


def test_apply_to_filter() -> None:
    c = _cfg(apply_to=["write_file"])
    r = apply_kernel_sandbox(c, cmd="echo", cwd=".", tool="run_command")
    assert r.applied is False
    assert r.backend == "skipped"


def test_local_backend_kernel_meta() -> None:
    from agent.execution.local import LocalExecutionBackend

    c = _cfg(backend="windows_restricted")
    backend = LocalExecutionBackend(c)
    with patch.object(WindowsRestrictedBackend, "available", return_value=True), patch(
        "subprocess.run"
    ) as mock_run:
        mock_run.return_value = MagicMock(stdout="ok", stderr="", returncode=0)
        result = backend.run(Path("."), "echo hi")
    assert result.meta.get("kernel_backend") == "windows_restricted"
    assert result.meta.get("isolation_level") == "kernel"


def test_probe_capabilities() -> None:
    cap = probe_capabilities(KernelSandboxSettings())
    assert "backend" in cap
    assert "platform" in cap


def test_windows_restricted_meta() -> None:
    s = KernelSandboxSettings()
    b = WindowsRestrictedBackend(s)
    r = b.wrap_command(cmd="dir", cwd="C:\\ws", sandbox_mode="workspace-write", settings=s)
    assert r.meta.get("use_restricted_token") is True


def test_kernel_disabled_when_master_off() -> None:
    c = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    c.sandbox_mode = SandboxMode.WORKSPACE_WRITE
    c.sandbox_kernel = KernelSandboxSettings(enabled=False)
    r = apply_kernel_sandbox(c, cmd="echo", cwd=".")
    assert r.applied is False


def test_factory_none_backend() -> None:
    assert select_kernel_backend(KernelSandboxSettings(backend="none")) is None


def test_metrics_on_apply() -> None:
    from agent.metrics import MetricsCollector

    MetricsCollector.reset_for_tests()
    c = _cfg(backend="windows_restricted")
    with patch.object(WindowsRestrictedBackend, "available", return_value=True):
        apply_kernel_sandbox(c, cmd="echo", cwd=".")
    body = MetricsCollector.global_collector().to_prometheus()
    assert "agent_sandbox_kernel_total" in body


def test_read_only_policy_label() -> None:
    from agent.sandbox.kernel.policy import policy_label

    assert policy_label(SandboxMode.READ_ONLY) == "read-only"


def test_macos_seatbelt_skips_non_darwin() -> None:
    from agent.sandbox.kernel.macos_seatbelt import MacosSeatbeltBackend

    b = MacosSeatbeltBackend(KernelSandboxSettings())
    with patch.object(sys, "platform", "linux"):
        assert b.available() is False


def test_macos_seatbelt_argv_when_available() -> None:
    from agent.sandbox.kernel.macos_seatbelt import MacosSeatbeltBackend

    b = MacosSeatbeltBackend(KernelSandboxSettings())
    with patch.object(sys, "platform", "darwin"), patch.object(b, "available", return_value=True):
        r = b.wrap_command(cmd="echo", cwd="/tmp", sandbox_mode="read-only", settings=KernelSandboxSettings())
    assert r.argv is not None
    assert "sandbox-exec" in r.argv[0]


def test_local_uses_argv_when_bwrap(tmp_path: Path) -> None:
    from agent.execution.local import LocalExecutionBackend

    c = _cfg(backend="bubblewrap")
    c.isolation.enabled = False
    backend = LocalExecutionBackend(c)
    with patch.object(LinuxBubblewrapBackend, "available", return_value=True), patch(
        "subprocess.run"
    ) as mock_run:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)
        backend.run(tmp_path, "echo test")
    args, kwargs = mock_run.call_args
    assert isinstance(args[0], list)
    assert kwargs.get("shell") is not True


def test_fallback_metrics() -> None:
    from agent.metrics import MetricsCollector

    MetricsCollector.reset_for_tests()
    c = _cfg(backend="bubblewrap", fail_open=True)
    with patch.object(LinuxBubblewrapBackend, "available", return_value=False):
        apply_kernel_sandbox(c, cmd="x", cwd=".")
    assert 'backend="fallback"' in MetricsCollector.global_collector().to_prometheus() or "fallback" in MetricsCollector.global_collector().to_prometheus()


def test_isolation_level_in_result() -> None:
    c = _cfg(backend="windows_restricted")
    with patch.object(WindowsRestrictedBackend, "available", return_value=True):
        r = apply_kernel_sandbox(c, cmd="echo", cwd=".")
    assert r.isolation_level == "kernel"


def test_bwrap_unshare_net_when_network_disabled() -> None:
    s = KernelSandboxSettings(
        linux=__import__("agent.sandbox.kernel", fromlist=["KernelLinuxSettings"]).KernelLinuxSettings(
            allow_network=False
        )
    )
    b = LinuxBubblewrapBackend(s)
    r = b.wrap_command(cmd="echo", cwd="/tmp", sandbox_mode="workspace-write", settings=s)
    assert "--unshare-net" in r.argv


def test_kernel_settings_loader() -> None:
    from agent.settings import load_kernel_sandbox_settings

    s = load_kernel_sandbox_settings()
    assert s.enabled is False
    assert s.backend == "auto"


def test_profiles_still_run_after_kernel() -> None:
    from agent.execution.local import LocalExecutionBackend

    c = _cfg(backend="windows_restricted")
    c.sandbox_profiles.enabled = True
    backend = LocalExecutionBackend(c)
    with patch.object(WindowsRestrictedBackend, "available", return_value=True), patch(
        "subprocess.run", return_value=MagicMock(stdout="", stderr="", returncode=0)
    ):
        result = backend.run(Path("."), "echo")
    assert "kernel_backend" in result.meta


def test_kernel_with_isolation_path() -> None:
    from agent.execution.local import LocalExecutionBackend

    c = _cfg(backend="windows_restricted")
    c.isolation.enabled = True
    backend = LocalExecutionBackend(c)
    with patch.object(WindowsRestrictedBackend, "available", return_value=True), patch(
        "subprocess.Popen"
    ) as mock_popen:
        proc = MagicMock()
        proc.pid = 123
        proc.communicate.return_value = ("ok\n", "")
        proc.returncode = 0
        mock_popen.return_value = proc
        result = backend.run(Path("."), "echo hi")
    assert result.meta.get("kernel_backend") == "windows_restricted"
    assert result.meta.get("isolation_level") == "kernel"
    assert result.meta.get("isolated") is True
