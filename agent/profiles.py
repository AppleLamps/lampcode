from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

from agent.paths import default_config_path


@dataclass
class ModelProfile:
    name: str
    model: str = ""
    max_tool_rounds: int | None = None
    reasoning_effort: str = ""
    fallback_models: list[str] = field(default_factory=list)


@dataclass
class RunProfile:
    name: str
    profile: str = "interactive"
    model_profile: str = ""
    sandbox_mode: str = ""
    approval_mode: str = ""
    max_tool_rounds: int | None = None
    model: str = ""


def project_config_path(cwd: Path) -> Path:
    return cwd / ".agent-cli" / "config.toml"


def user_config_path() -> Path:
    return default_config_path()


def _load_toml_dict(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)
    return data if isinstance(data, dict) else {}


def load_model_profiles(*paths: Path | None) -> dict[str, ModelProfile]:
    profiles: dict[str, ModelProfile] = {}
    for path in paths:
        if path is None:
            continue
        data = _load_toml_dict(path)
        raw = data.get("model_profiles", {})
        if not isinstance(raw, dict):
            continue
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            fb = cfg.get("fallback_models", [])
            profiles[str(name)] = ModelProfile(
                name=str(name),
                model=str(cfg.get("model", "")),
                max_tool_rounds=int(cfg["max_tool_rounds"]) if cfg.get("max_tool_rounds") is not None else None,
                reasoning_effort=str(cfg.get("reasoning_effort", "")),
                fallback_models=[str(x) for x in fb] if isinstance(fb, list) else [],
            )
    return profiles


def load_run_profiles(*paths: Path | None) -> dict[str, RunProfile]:
    profiles: dict[str, RunProfile] = {}
    for path in paths:
        if path is None:
            continue
        data = _load_toml_dict(path)
        raw = data.get("profiles", {})
        if not isinstance(raw, dict):
            continue
        for name, cfg in raw.items():
            if not isinstance(cfg, dict):
                continue
            profiles[str(name)] = RunProfile(
                name=str(name),
                profile=str(cfg.get("profile", name)),
                model_profile=str(cfg.get("model_profile", "")),
                sandbox_mode=str(cfg.get("sandbox_mode", "")),
                approval_mode=str(cfg.get("approval_mode", "")),
                max_tool_rounds=int(cfg["max_tool_rounds"]) if cfg.get("max_tool_rounds") is not None else None,
                model=str(cfg.get("model", "")),
            )
    return profiles


def merge_layered_config(
    cwd: Path,
    *,
    cli_profile: str | None = None,
    cli_model_profile: str | None = None,
    cli_model: str | None = None,
    user_path: Path | None = None,
) -> dict[str, Any]:
    """Precedence: CLI → named profile → project config → user config → defaults."""
    user_path = user_path or user_config_path()
    project_path = project_config_path(cwd)
    project_data = _load_toml_dict(project_path)
    user_data = _load_toml_dict(user_path)

    merged: dict[str, Any] = {}
    for src in (user_data, project_data):
        for key, val in src.items():
            if key not in ("model_profiles", "profiles", "project"):
                merged[key] = val

    profile_name = cli_profile or project_data.get("profile") or user_data.get("profile")
    if profile_name:
        run_profiles = {**load_run_profiles(user_path), **load_run_profiles(project_path)}
        rp = run_profiles.get(str(profile_name))
        if rp:
            if rp.model:
                merged["model"] = rp.model
            if rp.approval_mode:
                merged["approval_mode"] = rp.approval_mode
            if rp.sandbox_mode:
                merged["sandbox_mode"] = rp.sandbox_mode
            if rp.max_tool_rounds is not None:
                merged["max_tool_rounds"] = rp.max_tool_rounds
            if rp.model_profile and not cli_model_profile:
                merged["model_profile"] = rp.model_profile

    model_profile_name = cli_model_profile or merged.get("model_profile") or project_data.get(
        "model_profile"
    ) or user_data.get("model_profile")
    if model_profile_name:
        model_profiles = {
            **load_model_profiles(user_path),
            **load_model_profiles(project_path),
        }
        mp = model_profiles.get(str(model_profile_name))
        if mp:
            if mp.model:
                merged["model"] = mp.model
            if mp.max_tool_rounds is not None:
                merged["max_tool_rounds"] = mp.max_tool_rounds
            merged["_model_profile_fallbacks"] = mp.fallback_models
            merged["_reasoning_effort"] = mp.reasoning_effort

    if cli_model:
        merged["model"] = cli_model
    return merged


def apply_merged_to_resolve_kwargs(merged: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if merged.get("model"):
        out["model"] = merged["model"]
    if merged.get("max_tool_rounds") is not None:
        out["max_rounds"] = int(merged["max_tool_rounds"])
    if merged.get("approval_mode") in ("auto", "interactive"):
        out["auto_approve"] = merged["approval_mode"] == "auto"
    if merged.get("sandbox_mode"):
        out["sandbox"] = merged["sandbox_mode"]
    return out


def thread_cost_summary(thread) -> dict[str, Any]:
    total_in = 0
    total_out = 0
    total_cost = 0.0
    models_used: list[str] = []
    for turn in thread.turns:
        u = turn.usage
        total_in += u.input_tokens or 0
        total_out += u.output_tokens or 0
        if u.estimated_cost_usd:
            total_cost += u.estimated_cost_usd
        if u.model_used:
            models_used.append(u.model_used)
    return {
        "thread_id": thread.id,
        "turns": len(thread.turns),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "estimated_cost_usd": round(total_cost, 6),
        "models_used": models_used,
    }
