from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.metrics import MetricsCollector
from agent.sandbox.kernel import KernelSandboxSettings, KernelWindowsSettings, apply_kernel_sandbox
from agent.sandbox.kernel.factory import select_kernel_backend, select_windows_kernel_backend
from agent.sandbox.kernel.windows_appcontainer import (
    WindowsAppContainerBackend,
    probe_appcontainer,
    reset_appcontainer_probe_for_tests,
)
from agent.sandbox.policy import SandboxMode


@pytest.fixture(autouse=True)
def _reset() -> None:
    MetricsCollector.reset_for_tests()
    reset_appcontainer_probe_for_tests()
    yield
    MetricsCollector.reset_for_tests()
    reset_appcontainer_probe_for_tests()


def _settings(**kw) -> KernelSandboxSettings:
    win = kw.pop("windows", None) or KernelWindowsSettings()
    return KernelSandboxSettings(enabled=True, backend="auto", windows=win, **kw)


def _cfg(**kw) -> Config:
    c = Config(cwd=".", model="m", openrouter_api_key="x")
    c.sandbox_mode = SandboxMode.WORKSPACE_WRITE
    c.sandbox_kernel = _settings(**kw)
    return c


def test_probe_appcontainer_non_windows() -> None:
    with patch.object(sys, "platform", "linux"):
        p = probe_appcontainer()
    assert p["appcontainer"] == "unavailable"


def test_probe_appcontainer_forced_available() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    with patch.object(sys, "platform", "win32"):
        p = probe_appcontainer()
    assert p["appcontainer"] == "available"


def test_probe_appcontainer_forced_unavailable() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = False
    mod._force_unavailable_reason = "policy block"
    with patch.object(sys, "platform", "win32"), patch.object(
        mod, "_win32_build", return_value=22000
    ):
        p = probe_appcontainer()
    assert p["appcontainer"] == "unavailable"
    assert "policy" in p["reason"]


def test_appcontainer_backend_wrap_meta() -> None:
    backend = WindowsAppContainerBackend(_settings())
    result = backend.wrap_command(
        cmd="echo hi",
        cwd="C:\\proj",
        sandbox_mode="workspace-write",
        settings=_settings(),
    )
    assert result.applied is True
    assert result.backend == "windows_appcontainer"
    assert result.meta["wrapper"] == "windows_appcontainer"
    assert result.meta["launch_mode"] == "appcontainer"


def test_select_windows_preference_restricted() -> None:
    s = _settings(windows=KernelWindowsSettings(backend_preference="restricted"))
    with patch.object(sys, "platform", "win32"):
        b = select_windows_kernel_backend(s)
    assert b is not None
    assert b.name == "windows_restricted"


def test_select_windows_appcontainer_then_restricted_fallback() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = False
    s = _settings(windows=KernelWindowsSettings(backend_preference="appcontainer_then_restricted"))
    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=22000):
        b = select_windows_kernel_backend(s)
    assert b.name == "windows_restricted"


def test_select_windows_appcontainer_when_available() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    s = _settings(windows=KernelWindowsSettings(backend_preference="appcontainer_then_restricted"))
    with patch.object(sys, "platform", "win32"):
        b = select_windows_kernel_backend(s)
    assert b.name == "windows_appcontainer"


def test_factory_auto_win32_selects_appcontainer() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    with patch.object(sys, "platform", "win32"):
        b = select_kernel_backend(_settings())
    assert b.name == "windows_appcontainer"


def test_apply_kernel_appcontainer_emits_applied() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    events: list[str] = []

    class E:
        def sandbox_kernel_selected(self, *a, **k):
            events.append("selected")

        def sandbox_kernel_applied(self, *a, **k):
            events.append("applied")

        def sandbox_kernel_fallback(self, *a, **k):
            events.append("fallback")

    with patch.object(sys, "platform", "win32"):
        r = apply_kernel_sandbox(_cfg(), cmd="dir", cwd=".", emitter=E())
    assert r.applied
    assert r.backend == "windows_appcontainer"
    assert "applied" in events


def test_apply_kernel_appcontainer_metrics() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    with patch.object(sys, "platform", "win32"):
        apply_kernel_sandbox(_cfg(), cmd="dir", cwd=".")
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters["agent_sandbox_appcontainer_total"].get("applied", 0) >= 1


def test_apply_kernel_fallback_to_restricted() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = False
    events: list[str] = []

    class E:
        def sandbox_kernel_selected(self, *a, **k):
            pass

        def sandbox_kernel_applied(self, *a, **k):
            events.append("applied")

        def sandbox_kernel_fallback(self, *a, **k):
            events.append("fallback")

    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=22000):
        r = apply_kernel_sandbox(_cfg(), cmd="dir", cwd=".", emitter=E())
    assert r.applied
    assert r.backend == "windows_restricted"
    assert "fallback" in events


def test_apply_kernel_fallback_metric() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = False
    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=22000):
        apply_kernel_sandbox(_cfg(), cmd="dir", cwd=".")
    snap = MetricsCollector.global_collector().snapshot()
    assert snap.labeled_counters["agent_sandbox_appcontainer_total"].get("fallback", 0) >= 1


def test_appcontainer_unavailable_env_flag() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = None
    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=22000), patch.dict(
        os.environ, {"AGENT_APPCONTAINER_DISABLED": "1"}
    ):
        ok, reason = mod._check_win32_appcontainer_api()
    assert ok is False


def test_doctor_probe_includes_appcontainer() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod
    from agent.sandbox.kernel.doctor import probe_capabilities

    mod._force_available = True
    with patch.object(sys, "platform", "win32"):
        cap = probe_capabilities(_settings())
    assert "appcontainer" in cap


def test_read_only_mode_meta() -> None:
    backend = WindowsAppContainerBackend(_settings())
    result = backend.wrap_command(
        cmd="type file",
        cwd=".",
        sandbox_mode="read-only",
        settings=_settings(),
    )
    assert result.meta["read_only"] is True


def test_workspace_cap_default() -> None:
    backend = WindowsAppContainerBackend(_settings())
    result = backend.wrap_command(
        cmd="echo",
        cwd=".",
        sandbox_mode="workspace-write",
        settings=_settings(),
    )
    assert result.meta["workspace_cap"] is True


def test_backend_preference_appcontainer_only_none_when_unavailable() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = False
    s = _settings(windows=KernelWindowsSettings(backend_preference="appcontainer"))
    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=22000):
        b = select_windows_kernel_backend(s)
    assert b is None


def test_explicit_windows_appcontainer_backend() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = True
    s = KernelSandboxSettings(enabled=True, backend="windows_appcontainer")
    with patch.object(sys, "platform", "win32"):
        b = select_kernel_backend(s)
    assert b is not None
    assert b.name == "windows_appcontainer"


def test_old_build_unavailable() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    mod._force_available = None
    with patch.object(sys, "platform", "win32"), patch.object(mod, "_win32_build", return_value=9200):
        ok, _ = mod._check_win32_appcontainer_api()
    assert ok is False


@pytest.mark.skipif(os.environ.get("AGENT_TEST_APPCONTAINER") != "1", reason="optional integration")
def test_appcontainer_integration_optional() -> None:
    import agent.sandbox.kernel.windows_appcontainer as mod

    if sys.platform != "win32":
        pytest.skip("Windows only")
    ok, _ = mod._check_win32_appcontainer_api()
    assert ok is True
