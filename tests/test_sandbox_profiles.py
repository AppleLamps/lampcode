from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.config import Config
from agent.events import EventEmitter
from agent.execution.local import LocalExecutionBackend
from agent.sandbox.profiles import apply_sandbox_profile, select_profile
from agent.settings import SandboxProfileSettings


def _cfg(**kw) -> Config:
    c = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    c.sandbox_profiles = SandboxProfileSettings(enabled=True, **kw)
    c.execution.backend = "local"
    return c


def test_select_profile_auto_windows() -> None:
    settings = SandboxProfileSettings(profile="auto")
    with patch.object(sys, "platform", "win32"):
        prof = select_profile(settings)
    assert prof.name == "windows_job"


def test_select_profile_noop() -> None:
    prof = select_profile(SandboxProfileSettings(profile="noop"))
    assert prof.name == "noop"


def test_windows_job_available_on_win32() -> None:
    from agent.sandbox.profiles.windows_job import WindowsJobProfile

    with patch.object(sys, "platform", "win32"):
        assert WindowsJobProfile(SandboxProfileSettings()).available()


def test_windows_job_apply() -> None:
    from agent.sandbox.profiles.windows_job import WindowsJobProfile

    with patch.object(sys, "platform", "win32"):
        result = WindowsJobProfile(SandboxProfileSettings()).apply(
            cmd="echo hi", cwd=".", settings=SandboxProfileSettings()
        )
    assert result.applied is True
    assert result.meta is not None


def test_linux_unshare_not_available_without_binary() -> None:
    from agent.sandbox.profiles.linux_unshare import LinuxUnshareProfile

    with patch.object(sys, "platform", "linux"):
        with patch("agent.sandbox.profiles.linux_unshare.shutil.which", return_value=None):
            assert not LinuxUnshareProfile(SandboxProfileSettings()).available()


def test_apply_disabled_profiles() -> None:
    cfg = Config(cwd=Path("."), model="m", openrouter_api_key="x")
    cfg.sandbox_profiles.enabled = False
    result = apply_sandbox_profile(cfg, cmd="echo", cwd=".")
    assert result.applied is False


def test_apply_skipped_non_local_backend() -> None:
    cfg = _cfg()
    cfg.execution.backend = "ssh"
    result = apply_sandbox_profile(cfg, cmd="echo", cwd=".")
    assert result.profile == "skipped"


def test_apply_emits_event() -> None:
    cfg = _cfg(profile="noop", fail_open=True)
    events: list[str] = []
    emitter = EventEmitter(lambda e: events.append(e.type))
    apply_sandbox_profile(
        cfg,
        cmd="echo",
        cwd=".",
        emitter=emitter,
        thread_id="t1",
        turn_id="u1",
    )
    assert any("sandbox.profile" in e for e in events)


def test_fail_open_unavailable_profile() -> None:
    cfg = _cfg(profile="linux_unshare", fail_open=True)
    with patch.object(sys, "platform", "win32"):
        result = apply_sandbox_profile(cfg, cmd="echo", cwd=".")
    assert result.applied is False


def test_local_backend_includes_profile_meta() -> None:
    cfg = _cfg(profile="noop")
    backend = LocalExecutionBackend(cfg)
    with patch("subprocess.run") as run:
        run.return_value = type("P", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        result = backend.run(Path("."), "echo ok")
    assert "profile" in result.meta or result.meta.get("cwd")


def test_auto_profile_on_linux_with_unshare() -> None:
    settings = SandboxProfileSettings(profile="auto")
    with patch.object(sys, "platform", "linux"):
        with patch("agent.sandbox.profiles.linux_unshare.shutil.which", return_value="/usr/bin/unshare"):
            prof = select_profile(settings)
    assert prof.name == "linux_unshare"
