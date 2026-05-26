from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import Config, DEFAULT_MODEL
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


def classify_http_status(status: int, body: str = "") -> str:
    if status == 429:
        return "rate_limit"
    if status in (502, 503, 504):
        return "provider_error"
    if status == 404:
        return "model_not_found"
    if status >= 500:
        return "provider_error"
    if status == 400:
        lower = body.lower()
        if any(
            token in lower
            for token in ("context length", "context_length", "maximum context", "too many tokens")
        ):
            return "context_length"
    return "client_error"


def is_openrouter_api(base_url: str) -> bool:
    return "openrouter.ai" in base_url.rstrip("/").lower()


def use_native_model_routing(config: Config, chain: ModelChainResult) -> bool:
    settings = config.openrouter
    return (
        settings.native_fallback
        and is_openrouter_api(config.openrouter_base_url)
        and len(chain.models) > 1
    )


def build_response_format(schema: dict[str, Any], *, name: str = "agent_output") -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


def model_supports_structured_outputs(
    model_id: str, models: list[dict[str, Any]] | None = None
) -> bool:
    record = _model_record(model_id, models)
    if not record:
        return False
    supported = record.get("supported_parameters") or record.get("supportedParameters") or []
    if isinstance(supported, list):
        params = {str(p).lower() for p in supported}
        return "structured_outputs" in params or "response_format" in params
    return False


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
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    inp = (usage or {}).get("prompt_tokens")
    out = (usage or {}).get("completion_tokens")
    api_cost = (usage or {}).get("cost")
    if api_cost is not None:
        cost = float(api_cost)
        cost_source = "api"
    else:
        cost = estimate_cost_usd(model_used, inp, out, config.openrouter.pricing)
        cost_source = "pricing_seed"
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "estimated_cost_usd": round(cost, 6),
        "cost_source": cost_source,
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
    health = check_openrouter_health(api_key, http_get=http_get)
    if health.key_validity == "missing":
        return ("missing", "set OPENROUTER_API_KEY")
    if health.reachability != "ok":
        return (health.reachability, health.detail)
    if health.key_validity != "ok":
        return (health.key_validity, health.detail)
    return ("reachable", health.detail)


@dataclass
class OpenRouterHealth:
    reachability: str  # ok | unreachable | error
    key_validity: str  # ok | invalid | missing | unknown
    detail: str = ""


def check_openrouter_health(
    api_key: str | None,
    *,
    http_get: Any = None,
) -> OpenRouterHealth:
    """Split reachability vs API key validity for agent doctor."""
    if not api_key:
        return OpenRouterHealth("unknown", "missing", "set OPENROUTER_API_KEY")
    get = http_get or _default_get
    try:
        resp = get("https://openrouter.ai/api/v1/models", api_key=api_key)
        code = int(getattr(resp, "status_code", 200))
        if code == 200:
            return OpenRouterHealth("ok", "ok", "models API ok")
        if code == 401:
            return OpenRouterHealth("ok", "invalid", "invalid API key (401)")
        if code == 403:
            return OpenRouterHealth("ok", "invalid", "forbidden (403)")
        return OpenRouterHealth("error", "unknown", f"HTTP {code}")
    except Exception as exc:
        return OpenRouterHealth("unreachable", "unknown", str(exc)[:120])


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
        return str(models[0].get("id", DEFAULT_MODEL))
    return DEFAULT_MODEL


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
    if any(x in mid for x in ("claude", "gpt", "gemini", "mistral", "deepseek", "minimax")):
        return True
    return False


def tool_support_warning(model_id: str, models: list[dict[str, Any]] | None = None) -> str | None:
    if model_supports_tools(model_id, models):
        return None
    return (
        f"Model {model_id!r} may not support tool calling — "
        "agent run/repl tools may fail. Pick a model with tool support."
    )


def _model_record(model_id: str, models: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not models:
        return None
    mid = model_id.lower()
    for m in models:
        if str(m.get("id", "")).lower() == mid:
            return m
    return None


def model_context_window(model_id: str, models: list[dict[str, Any]] | None = None) -> int | None:
    record = _model_record(model_id, models)
    if not record:
        return None
    top = record.get("top_provider") or {}
    if isinstance(top, dict) and top.get("context_length"):
        return int(top["context_length"])
    if record.get("context_length"):
        return int(record["context_length"])
    return None


def model_has_vision(model_id: str, models: list[dict[str, Any]] | None = None) -> bool | None:
    record = _model_record(model_id, models)
    if not record:
        return None
    arch = record.get("architecture") or {}
    if isinstance(arch, dict):
        modality = str(arch.get("modality", "")).lower()
        if modality:
            return "image" in modality or "vision" in modality
    return None


def model_has_reasoning(model_id: str, models: list[dict[str, Any]] | None = None) -> bool | None:
    record = _model_record(model_id, models)
    if not record:
        return None
    supported = record.get("supported_parameters") or record.get("supportedParameters") or []
    if isinstance(supported, list):
        params = {str(p).lower() for p in supported}
        return "reasoning" in params or "include_reasoning" in params
    return None


def model_price_per_million(
    model_id: str,
    pricing: dict[str, SwarmBudgetPricing],
    *,
    direction: str,
) -> float | None:
    price = pricing.get(model_id)
    if not price:
        for key, val in pricing.items():
            if key in model_id or model_id.endswith(key.split("/")[-1]):
                price = val
                break
    if not price:
        return None
    return price.input_per_million if direction == "input" else price.output_per_million


def build_model_preflight_rows(
    *,
    profiles: dict[str, Any],
    default_model: str,
    models: list[dict[str, Any]] | None,
    pricing: dict[str, SwarmBudgetPricing],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_row(profile: str, model_id: str) -> None:
        if not model_id or model_id in seen:
            return
        seen.add(model_id)
        tools = "yes" if model_supports_tools(model_id, models) else "no"
        ctx = model_context_window(model_id, models)
        ctx_str = f"{ctx:,}" if ctx else "—"
        inp = model_price_per_million(model_id, pricing, direction="input")
        out = model_price_per_million(model_id, pricing, direction="output")
        vision = model_has_vision(model_id, models)
        reasoning = model_has_reasoning(model_id, models)
        rows.append(
            {
                "profile": profile,
                "model": model_id,
                "tools": tools,
                "context": ctx_str,
                "input_$1m": f"{inp:.2f}" if inp is not None else "—",
                "output_$1m": f"{out:.2f}" if out is not None else "—",
                "vision": "yes" if vision else ("no" if vision is False else "—"),
                "reasoning": "yes" if reasoning else ("no" if reasoning is False else "—"),
            }
        )

    for name, profile in profiles.items():
        add_row(name, getattr(profile, "model", "") or "")
    add_row("(default)", default_model)
    return rows
