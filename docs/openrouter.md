# OpenRouter integration

agent-cli uses the [OpenRouter](https://openrouter.ai) chat completions API (`/chat/completions`) with streaming, tool calling, retries, and optional native model routing.

## Authentication

Set `OPENROUTER_API_KEY` in the environment or `.env` (auto-loaded). Optional headers come from config:

```toml
[openrouter]
app_name = "agent-cli"
app_url = "https://github.com/agent-cli"
```

These map to `X-Title` and `HTTP-Referer` on each request.

## Model selection and routing

| Layer | Config | Purpose |
|-------|--------|---------|
| Default model | `model = "…"` | Primary model for runs |
| Profile override | `[model_profiles.deep] model = "…"` | `--model-profile deep` |
| Task routing | `[model_routing] rules = […]` | Auto-pick profile from user text |
| OpenRouter primary | `[openrouter] primary_model = "…"` | Overrides top-level `model` for API calls |
| Profile fallbacks | `[model_profiles.*] fallback_models = […]` | Per-profile backup models |
| OpenRouter fallbacks | `[openrouter] fallback_models = […]` | Global backup models |

The effective chain is deduplicated: `primary → openrouter.fallback_models → profile fallbacks`.

## Fallback routing

Two modes (controlled by `native_fallback`, default `true`):

### Native fallback (default)

When the base URL is OpenRouter and there are multiple models, agent-cli sends **one request** with:

```json
{
  "model": "primary/model",
  "models": ["fallback/a", "fallback/b"],
  "route": "fallback"
}
```

OpenRouter picks the first available model. The response `model` field and `fallback_used` on the turn reflect which model actually served the request.

### Client-side fallback

Set `native_fallback = false` to retry sequentially on the client (legacy behavior). Useful for non-OpenRouter-compatible proxies or debugging per-model errors.

### When to fall back

`fallback_on` lists error kinds that trigger the next model (client-side) or are classified for hints (native):

| Kind | Typical trigger |
|------|-----------------|
| `rate_limit` | HTTP 429 |
| `provider_error` | HTTP 502/503/504 or 5xx |
| `timeout` | Request timeout |
| `context_length` | HTTP 400 with context-length message |

```toml
[openrouter]
fallback_models = ["openai/gpt-4.1", "google/gemini-2.5-pro-preview"]
fallback_on = ["rate_limit", "provider_error", "timeout", "context_length"]
native_fallback = true
```

## Retries

Transient errors (429, 502, 503, 504) retry on the **same** model before fallback applies:

```toml
[openrouter]
max_retries = 3
retry_base_delay_sec = 1.0
request_timeout_sec = 120
```

Backoff is exponential with jitter; `Retry-After` is honored when present.

## Reasoning models

When a model profile sets `reasoning_effort` (`low`, `medium`, `high`), the client sends:

```json
{ "reasoning": { "effort": "high", "exclude": true } }
```

`reasoning_exclude = true` (default) keeps reasoning out of the visible assistant message while still capturing it for multi-turn tool rounds.

During streaming, `reasoning` and `reasoning_details` deltas are merged and attached to assistant messages in the tool loop so follow-up rounds preserve chain-of-thought where the provider requires it.

## Structured output

`agent run --output-schema schema.json` validates the final assistant JSON against your schema. When the cached models API lists `structured_outputs` or `response_format` for the active model, agent-cli sends OpenRouter `response_format` with strict JSON schema. If the model rejects structured output, the client retries once without `response_format`.

## Cost tracking

Turn usage prefers OpenRouter's `usage.cost` from the API when present (`cost_source = "api"`). Otherwise cost is estimated from `[openrouter.pricing."model-id"]` seed values (`cost_source = "pricing_seed"`).

```toml
[openrouter.pricing."anthropic/claude-sonnet-4"]
input_per_million = 3.0
output_per_million = 15.0
```

End-of-run summary and `agent threads cost` use the enriched estimate.

## Optional request fields

```toml
[openrouter]
max_tokens = 8192          # cap completion tokens (optional)
user_id = "my-user-id"     # OpenRouter `user` field for abuse tracking
require_parameters = false # set provider.require_parameters when tools are sent
```

## Diagnostics

```powershell
agent doctor                    # OpenRouter reachability + key validity
agent doctor --models           # tools, context, pricing, vision, reasoning per profile
agent models list               # refresh ~/.agent-cli/cache/openrouter-models.json
agent models recommend --task "fix pytest"
```

Tool-capability warnings appear once per session when the active model may not support OpenAI-style tools.

## Full example

```toml
model = "minimax/minimax-m2.7"

[openrouter]
primary_model = "minimax/minimax-m2.7"
fallback_models = ["openai/gpt-4.1", "google/gemini-2.5-pro-preview"]
fallback_on = ["rate_limit", "provider_error", "timeout", "context_length"]
native_fallback = true
max_retries = 3
request_timeout_sec = 120
reasoning_exclude = true
max_tokens = 8192

[openrouter.pricing."anthropic/claude-sonnet-4"]
input_per_million = 3.0
output_per_million = 15.0

[model_profiles.deep]
model = "minimax/minimax-m2.7"
max_tool_rounds = 40
reasoning_effort = "high"
```
