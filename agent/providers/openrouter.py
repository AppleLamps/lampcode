from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import Config
from agent.model_routing import resolve_model_profile_from_task
from agent.settings import OpenRouterSettings, SwarmBudgetPricing


@dataclass
class ModelChainResult:
    models: list[str]
    primary: str


def model_chain(config: Config) -> ModelChainResult:
    settings: OpenRouterSettings = config.openrouter
    primary = config.model
    if settings.primary_model:
        primary = settings.primary_model
    extra = list(getattr(config, "model_profile_fallbacks", None) or [])
    fallbacks = list(settings.fallback_models or []) + extra
    seen: set[str] = set()
    ordered: list[str] = []
    for m in [primary, *fallbacks]:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ModelChainResult(models=ordered or [config.model], primary=ordered[0] if ordered else config.model)


def classify_http_status(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status in (502, 503, 504):
        return "provider_error"
    if status == 404:
        return "model_not_found"
    if status >= 500:
        return "provider_error"
    return "client_error"


def should_fallback(
    *,
    settings: OpenRouterSettings,
    error_kind: str,
    model_index: int,
    total_models: int,
) -> bool:
    if model_index >= total_models - 1:
        return False
    allowed = set(settings.fallback_on or [])
    if error_kind in allowed:
        return True
    if error_kind == "model_not_found" and "provider_error" in allowed:
        return True
    return False


def estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: dict[str, SwarmBudgetPricing],
) -> float:
    if not input_tokens and not output_tokens:
        return 0.0
    inp = input_tokens or 0
    out = output_tokens or 0
    price = pricing.get(model)
    if not price:
        for key, val in pricing.items():
            if key in model or model.endswith(key.split("/")[-1]):
                price = val
                break
    if not price:
        return 0.0
    return (inp * price.input_per_million / 1_000_000) + (
        out * price.output_per_million / 1_000_000
    )


def enrich_usage(
    config: Config,
    *,
    model_used: str,
    fallback_used: bool,
    usage: dict[str, int] | None,
) -> dict[str, Any]:
    inp = (usage or {}).get("prompt_tokens")
    out = (usage or {}).get("completion_tokens")
    cost = estimate_cost_usd(model_used, inp, out, config.openrouter.pricing)
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "estimated_cost_usd": round(cost, 6),
        "model_used": model_used,
        "fallback_used": fallback_used,
    }


DEFAULT_MODELS_CACHE = Path.home() / ".agent-cli" / "cache" / "openrouter-models.json"
CACHE_TTL_SEC = 3600


@dataclass
class ModelsCache:
    path: Path = field(default_factory=lambda: DEFAULT_MODELS_CACHE)
    ttl_sec: int = CACHE_TTL_SEC

    def load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if time.time() - float(data.get("fetched_at", 0)) > self.ttl_sec:
                return []
            return list(data.get("models", []))
        except (OSError, json.JSONDecodeError, TypeError):
            return []

    def save(self, models: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"fetched_at": time.time(), "models": models}, indent=2),
            encoding="utf-8",
        )

    def fetch(self, api_key: str, *, http_get: Any = None) -> list[dict[str, Any]]:
        get = http_get or _default_get
        resp = get("https://openrouter.ai/api/v1/models", api_key=api_key)
        if hasattr(resp, "json"):
            data = resp.json()
        else:
            data = resp
        models = data.get("data", data if isinstance(data, list) else [])
        if isinstance(models, list):
            self.save(models)
            return models
        return []


def _default_get(url: str, *, api_key: str) -> Any:
    import httpx

    return httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)


def probe_openrouter_reachability(
    api_key: str | None,
    *,
    http_get: Any = None,
) -> tuple[str, str]:
    """Lightweight OpenRouter API check for agent doctor."""
    if not api_key:
        return ("missing", "set OPENROUTER_API_KEY")
    get = http_get or _default_get
    try:
        resp = get("https://openrouter.ai/api/v1/models", api_key=api_key)
        code = int(getattr(resp, "status_code", 200))
        if code == 200:
            return ("reachable", "models API ok")
        if code == 401:
            return ("auth failed", "invalid API key")
        return ("error", f"HTTP {code}")
    except Exception as exc:
        return ("unreachable", str(exc)[:120])


def recommend_model(
    task: str,
    models: list[dict[str, Any]],
    *,
    cwd: Path | None = None,
) -> str:
    """Recommend model for task using routing rules + cache/heuristics."""
    from agent.profiles import load_model_profiles, project_config_path

    routed_profile = resolve_model_profile_from_task(task, cwd=cwd)
    if routed_profile:
        paths = [project_config_path(cwd)] if cwd else []
        profiles = load_model_profiles(*paths)
        mp = profiles.get(routed_profile)
        if mp and mp.model:
            return mp.model

    task_l = task.lower()
    scored: list[tuple[int, str]] = []
    for m in models:
        mid = str(m.get("id", ""))
        name = str(m.get("name", mid)).lower()
        score = 0
        if "code" in task_l or "test" in task_l or "fix" in task_l:
            if any(x in name for x in ("claude", "gpt", "codex", "sonnet")):
                score += 3
            if "flash" in name:
                score += 1
        if "fast" in task_l and "flash" in name:
            score += 4
        if score:
            scored.append((score, mid))
    if scored:
        scored.sort(key=lambda x: -x[0])
        return scored[0][1]
    if models:
        return str(models[0].get("id", "anthropic/claude-sonnet-4"))
    return "anthropic/claude-sonnet-4"


TOOL_UNSUPPORTED_DENYLIST: frozenset[str] = frozenset(
    {
        "openrouter/auto",
        "openrouter/free",
    }
)


def model_supports_tools(model_id: str, models: list[dict[str, Any]] | None = None) -> bool:
    """Heuristic: whether model likely supports OpenAI-style tool calling."""
    mid = model_id.lower()
    if mid in TOOL_UNSUPPORTED_DENYLIST:
        return False
    if models:
        for m in models:
            if str(m.get("id", "")).lower() != mid:
                continue
            arch = m.get("architecture") or {}
            if isinstance(arch, dict):
                modality = str(arch.get("modality", "")).lower()
                if modality and "text" in modality and "tool" not in modality:
                    pass
            supported = m.get("supported_parameters") or m.get("supportedParameters") or []
            if isinstance(supported, list):
                params = {str(p).lower() for p in supported}
                if params and "tools" not in params and "tool_choice" not in params:
                    return False
            return True
    # Conservative allowlist for common harness models without cache
    if any(x in mid for x in ("claude", "gpt", "gemini", "mistral", "deepseek")):
        return True
    return False


def tool_support_warning(model_id: str, models: list[dict[str, Any]] | None = None) -> str | None:
    if model_supports_tools(model_id, models):
        return None
    return (
        f"Model {model_id!r} may not support tool calling — "
        "agent run/repl tools may fail. Pick a model with tool support."
    )
