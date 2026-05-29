# Implementation Plan

[Overview]
Implement the three highest-priority hardening and scalability fixes identified in the code review: safer execution defaults and sandbox enforcement, hardened HTTP serve authentication/control behavior, and more scalable/concurrency-safe thread persistence.

The codebase is a Python 3.11+ local coding-agent CLI with tool dispatch, shell execution, optional Docker/SSH backends, HTTP dashboard/serve mode, and JSONL-backed local conversation storage. The highest-risk behavior is concentrated around model-driven command execution (`tools/registry.py`, `agent/execution/*`, `agent/sandbox/*`), the HTTP serve surface (`agent/serve/*`), and the JSONL thread store (`agent/store.py`). The goal of this implementation is to reduce unsafe-by-default behavior without removing existing capabilities, by adding explicit opt-ins, stricter validation, safer HTTP/session behavior, and storage hot-path improvements.

The implementation should be backward-aware but security-forward. Existing users should receive warnings or compatibility flags where behavior changes may be surprising, but the default posture should favor interactive approval, no query-token auth, bounded HTTP request sizes, metadata-only thread listings, and file-lock/atomic-write protection around thread store writes. This plan intentionally avoids adding heavy external dependencies unless they are optional, because the project currently has a small dependency footprint in `pyproject.toml`.

[Types]
The type changes add explicit settings for execution hardening, HTTP auth/body limits, and thread store locking/metadata behavior.

Update `agent/settings.py` dataclasses as follows:

```python
@dataclass
class ExecutionSettings:
    backend: str = "local"
    default_image: str = "python:3.12-slim"
    workspace_mount: str = "/workspace"
    network: str = "none"
    memory_limit: str = "1g"
    cpu_limit: str = "1.0"
    command_timeout_sec: int = 120
    auto_background_servers: bool = True
    auto_pull: bool = False
    prefer_hardened_backend: bool = False
    warn_on_unisolated_local: bool = True
    require_interactive_for_danger_full_access: bool = True
    docker: DockerExecutionSettings = field(default_factory=DockerExecutionSettings)
    ssh: SshExecutionSettings = field(default_factory=SshExecutionSettings)
    docker_image_override: str | None = None
```

Validation rules:
- `prefer_hardened_backend` defaults to `False` to avoid breaking local workflows, but when `True` and Docker is available, command execution should prefer Docker for `workspace-write` unless `execution.backend` was explicitly set.
- `warn_on_unisolated_local` controls user-facing warnings when local execution is not protected by `config.use_isolation`, kernel sandbox, Docker, or SSH.
- `require_interactive_for_danger_full_access` prevents silent `approval_mode="auto"` with `sandbox_mode="danger-full-access"` unless the user explicitly passes `--auto-approve` or configures an override.

```python
@dataclass
class DockerExecutionSettings:
    binary: str = "docker"
    platform: str = ""
    file_tools_in_container: bool = False
    read_only_rootfs: bool = True
    user: str = "65532:65532"
    cap_drop_all: bool = True
    security_opt_no_new_privileges: bool = True
```

Validation rules:
- `read_only_rootfs=True` adds `--read-only` while still allowing the bind-mounted workspace to be writable according to sandbox mode.
- `user` may be empty to preserve legacy image compatibility, but defaults to a non-root numeric user.
- `cap_drop_all` adds `--cap-drop=ALL`.
- `security_opt_no_new_privileges` adds `--security-opt=no-new-privileges`.

```python
@dataclass
class ServeSettings:
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""
    auth_mode: str = "bearer"
    allow_remote_bind: bool = False
    allow_query_tokens: bool = False
    enable_control: bool = True
    enable_turn_start: bool = False
    max_concurrent_turns: int = 2
    approval_timeout_sec: int = 300
    stream_buffer_size: int = 256
    cors: bool = False
    cors_allowed_origins: list[str] = field(default_factory=list)
    max_request_body_bytes: int = 1_048_576
    redact_thread_responses: bool = True
    session_ttl_sec: int = 28800
    session_persist: bool = True
    tls: ServeTlsSettings = field(default_factory=ServeTlsSettings)
    rbac: ServeRbacSettings = field(default_factory=ServeRbacSettings)
    oidc: "ServeOidcSettings | None" = None
    ide: ServeIdeSettings = field(default_factory=ServeIdeSettings)
    policy: ServePolicySettings = field(default_factory=ServePolicySettings)
    webhooks: ServeWebhookSettings = field(default_factory=ServeWebhookSettings)
```

Validation rules:
- `allow_query_tokens=False` means `?token=` and `?session=` are ignored unless explicitly enabled.
- `cors_allowed_origins` is used only when `cors=True`; wildcard `*` should be rejected or ignored when session/OIDC auth is active.
- `max_request_body_bytes` applies to every HTTP POST/PUT route before reading from `rfile`.
- `redact_thread_responses=True` causes default thread/run JSON responses to omit full command outputs, agent text, and file content previews unless an explicit `?full=1` is requested by an admin principal.

```python
@dataclass
class ThreadStoreSettings:
    lock_timeout_sec: float = 5.0
    metadata_sidecar: bool = True
    compact_on_rewrite: bool = True
```

This may live in `agent/store.py` or `agent/settings.py`; prefer `agent/store.py` if used only by `ThreadStore`. Validation rules:
- `lock_timeout_sec` must be positive.
- `metadata_sidecar=True` writes `<thread_id>.meta.json` alongside `<thread_id>.jsonl`.
- `compact_on_rewrite=True` preserves the current `rewrite_turns()` behavior while making it atomic.

[Files]
The implementation modifies focused execution, serve, and storage files while adding targeted tests for security and performance regressions.

New files to create:
- `agent-cli/agent/serve/request_limits.py` — helper functions for bounded request-body reads and safe JSON parsing.
- `agent-cli/agent/store_lock.py` — small cross-platform lock abstraction using lock files and atomic creation, with Windows-compatible fallback behavior.
- `agent-cli/tests/test_serve_hardening.py` — tests for disabled query tokens, request body limits, cookie `Secure`, and restricted CORS.
- `agent-cli/tests/test_store_scalability.py` — tests for metadata sidecar reads, append behavior avoiding full rewrites, and concurrent append safety.
- `agent-cli/tests/test_execution_hardening.py` — tests for Docker hardening flags, danger-full-access approval safeguards, and warnings for unisolated local execution.

Existing files to modify:
- `agent-cli/agent/config.py`
  - Change effective default approval mode from auto to interactive when no CLI/env/config value is provided.
  - Add validation/warning metadata for unsafe combinations such as `danger-full-access` + auto approval.
  - Load new execution settings fields.
- `agent-cli/agent/settings.py`
  - Add new dataclass fields listed in `[Types]`.
  - Parse new TOML keys in `load_execution_settings()` and `load_serve_settings()`.
  - Ensure defaults are secure and backward-compatible where possible.
- `agent-cli/agent/execution/docker.py`
  - Extend `build_docker_run_argv()` with read-only rootfs, non-root user, dropped capabilities, and `no-new-privileges` flags.
  - Ensure workspace mount write mode remains governed by sandbox mode.
- `agent-cli/agent/execution/local.py`
  - Emit metadata indicating when local execution is unisolated.
  - Preserve current behavior but make unsafe mode observable for CLI/serve warnings.
- `agent-cli/agent/sandbox/enforcer.py`
  - Add explicit detection tests for nested shell/language interpreter write patterns as defense-in-depth.
  - Keep classifier as advisory; do not claim it is a hard sandbox.
- `agent-cli/cli/main.py`
  - Print concise warnings when execution is unisolated or when the user has opted into dangerous combinations.
  - Ensure `--auto-approve` remains an explicit user action.
- `agent-cli/agent/serve/auth.py`
  - Add an `allow_query_tokens` parameter to `authorize_request_v2()`.
  - Ignore query tokens unless the setting is explicitly enabled.
- `agent-cli/agent/serve/routes/auth_routes.py`
  - Pass `allow_query_tokens=ctx.settings.allow_query_tokens`.
  - Use bounded body-read helpers for `/auth/login`, device poll, webhooks, and other POST handlers.
  - Add `Secure` to session cookies when TLS is enabled.
- `agent-cli/agent/serve/http_response.py`
  - Replace wildcard CORS behavior with explicit origin matching.
  - Add redacted thread serialization helper.
  - Use metadata-only fallback for prefix thread matching.
- `agent-cli/agent/serve/server.py`
  - Prefer `list_thread_meta()` for dashboard and `/threads` listing.
  - Enforce remote-bind safety checks at startup.
- `agent-cli/agent/serve/routes/thread_routes.py`
  - Use bounded body-read helpers for `/threads/{id}/run`, `/approvals/{id}`, and `/sync/resolve`.
  - Add pagination/limits where full event streams are returned.
- `agent-cli/agent/serve/routes/ide_routes.py`
  - Use bounded body-read helpers for IDE file writes.
- `agent-cli/agent/store.py`
  - Add metadata sidecar writes.
  - Make append/rewrite operations atomic and locked.
  - Prefer metadata-only operations for listing and prefix lookup.
- `agent-cli/README.md`
  - Update sandbox/serve documentation to accurately describe hardening defaults and explicit opt-ins.

Files to avoid deleting or moving:
- Do not remove existing JSONL thread files or change their on-disk format incompatibly.
- Do not remove `list_threads()`; update callers to use `list_thread_meta()` where full turn data is not needed.

Configuration file updates:
- `pyproject.toml` does not require new runtime dependencies for the planned implementation.
- If a lock implementation chooses `portalocker` or another dependency, add it only after confirming it is necessary; prefer stdlib lock-file semantics first.

[Functions]
The function changes introduce bounded reads, safer auth handling, hardened Docker argv generation, and metadata-only store operations.

New functions:
- `agent.serve.request_limits.read_limited_body(handler, *, max_bytes: int, default: bytes = b"{}") -> bytes`
  - Reads `Content-Length` safely.
  - Rejects negative, missing-but-required, or oversized bodies by raising a typed error or returning an HTTP-ready error object.
- `agent.serve.request_limits.read_limited_json(handler, *, max_bytes: int) -> dict[str, Any]`
  - Uses `read_limited_body()` and parses JSON.
  - Raises/returns a 400 error on invalid JSON.
- `agent.serve.http_response.thread_to_redacted_dict(thread: Thread, *, include_items: bool = True) -> dict[str, Any]`
  - Omits or truncates sensitive fields: command output, file content, large diffs, agent messages, and user prompt bodies.
- `agent.store_lock.acquire_file_lock(path: Path, *, timeout_sec: float) -> ContextManager[None]`
  - Creates a lock file atomically.
  - Removes stale locks conservatively if the owning process no longer exists where detectable.
- `agent.store.ThreadStore.thread_meta_path(thread_id: str) -> Path`
  - Returns sidecar metadata path `<thread_id>.meta.json`.
- `agent.store.ThreadStore.write_thread_meta(thread: Thread) -> None`
  - Writes metadata atomically to sidecar.
- `agent.store.ThreadStore.find_matches_by_prefix(thread_id_or_prefix: str, *, meta_only: bool = True) -> list[Thread]`
  - Uses metadata sidecars or first-line metadata to avoid loading all thread turns.

Modified functions:
- `agent.config.Config.resolve(...) -> Config`
  - Change default fallback approval mode to interactive.
  - Apply validation logic for unsafe sandbox/approval combinations.
  - Ensure env/config parsing still honors explicit `AGENT_APPROVAL_MODE=auto` and `--auto-approve`.
- `agent.execution.docker.build_docker_run_argv(...) -> list[str]`
  - Add hardened Docker flags from `DockerExecutionSettings`.
  - Preserve existing image, mount, workdir, resource, platform, and network behavior.
- `agent.execution.local.LocalExecutionBackend._execute_run(...) -> ExecutionResult`
  - Add `meta["isolated"] = False` and `meta["unisolated_local"] = True` when no hard isolation applies.
- `agent.serve.auth.extract_query_token(path: str) -> str | None`
  - Keep for compatibility, but call it only when `allow_query_tokens=True`.
- `agent.serve.auth.authorize_request_v2(...) -> AuthResult`
  - Add keyword-only `allow_query_tokens: bool = False`.
  - Use `extract_bearer_token(headers)` by default; append `or extract_query_token(path)` only when allowed.
- `agent.serve.routes.auth_routes.AuthRoutesMixin._authorize(self) -> bool`
  - Pass `allow_query_tokens` from settings.
  - Ensure policy enforcement remains after authentication.
- `agent.serve.routes.auth_routes.AuthRoutesMixin._oidc_callback(self) -> None`
  - Add `Secure` cookie attribute when `ctx.settings.tls.enabled` is true.
- `agent.serve.http_response.HttpResponseMixin._json_response(...) -> None`
  - Replace wildcard CORS with validated explicit origin response.
- `agent.serve.http_response.HttpResponseMixin._load_thread(self, thread_id: str) -> Thread`
  - Use metadata-only prefix matching and only load the selected full thread after a unique match is found.
- `agent.store.ThreadStore.append_item(...) -> None`
  - Lock the thread file, append item, and update metadata sidecar without reading/re-writing the full JSONL.
- `agent.store.ThreadStore.append_turn(...) -> None`
  - Same locking/metadata-sidecar behavior.
- `agent.store.ThreadStore.save_thread(...) -> None`
  - Avoid rewriting existing records solely to update metadata; update sidecar instead.
- `agent.store.ThreadStore.list_threads(...) -> list[Thread]`
  - Keep full behavior, but document it as heavy and update routes not to call it for summaries.
- `agent.store.ThreadStore.list_thread_meta(...) -> list[Thread]`
  - Prefer sidecar metadata if present; fall back to first-line JSONL meta.

Removed functions:
- None. Keep legacy functions to preserve API compatibility.

[Classes]
The class changes are incremental extensions to existing configuration, HTTP mixin, execution backend, and store classes.

New classes:
- `agent.serve.request_limits.RequestBodyTooLarge(Exception)`
  - Fields: `limit: int`, `actual: int | None`.
  - Used by serve route handlers to return HTTP 413.
- `agent.serve.request_limits.InvalidRequestBody(Exception)`
  - Fields: `message: str`, `status: int = 400`.
  - Used for malformed `Content-Length` or invalid JSON.
- Optional `agent.store_lock.FileLock`
  - Methods: `__enter__`, `__exit__`.
  - Encapsulates lock-file path, timeout, and cleanup.

Modified classes:
- `agent.settings.ExecutionSettings`
  - Add `prefer_hardened_backend`, `warn_on_unisolated_local`, and `require_interactive_for_danger_full_access`.
- `agent.settings.DockerExecutionSettings`
  - Add hardening fields: `read_only_rootfs`, `user`, `cap_drop_all`, `security_opt_no_new_privileges`.
- `agent.settings.ServeSettings`
  - Add `allow_query_tokens`, `cors_allowed_origins`, `max_request_body_bytes`, and `redact_thread_responses`.
- `agent.execution.local.LocalExecutionBackend`
  - Include explicit metadata for unisolated local runs.
- `agent.execution.docker.DockerExecutionBackend`
  - Consume new Docker settings through `build_docker_run_argv()`.
- `agent.serve.http_response.HttpResponseMixin`
  - Add safe CORS handling and redacted serialization helpers.
- `agent.store.ThreadStore`
  - Add metadata sidecar methods, lock usage, and atomic writes.

Removed classes:
- None.

[Dependencies]
The implementation should avoid new runtime dependencies unless a stdlib locking approach proves insufficient.

No required dependency changes are planned. The existing dependency set in `pyproject.toml` is sufficient for request limiting, CORS header handling, Docker argv construction, and JSONL metadata sidecars. File locking can be implemented with stdlib atomic file creation using `os.open(..., os.O_CREAT | os.O_EXCL | os.O_WRONLY)` and timeout polling, which works on Windows and POSIX for this local use case.

Optional dependency consideration:
- If stdlib lock-file implementation becomes too brittle, consider adding `portalocker>=2.8.0` to dependencies. This should be treated as a fallback, not the first choice.

[Testing]
The testing approach adds regression coverage for unsafe defaults, serve hardening, and storage scalability/concurrency without requiring network or Docker daemon integration.

New tests in `tests/test_execution_hardening.py`:
- Verify default `Config.resolve()` uses `approval_mode="interactive"` when no config/env/CLI override is present.
- Verify explicit `auto_approve=True` still results in `approval_mode="auto"`.
- Verify `build_docker_run_argv()` includes hardening flags by default:
  - `--read-only`
  - `--cap-drop=ALL`
  - `--security-opt=no-new-privileges`
  - `--user 65532:65532`
  - `--network none`
- Verify Docker read-only sandbox still creates a read-only workspace mount.
- Verify local execution metadata marks unisolated runs when isolation is disabled.

New tests in `tests/test_serve_hardening.py`:
- `authorize_request_v2()` rejects `?token=` by default even when the token matches.
- `authorize_request_v2(..., allow_query_tokens=True)` accepts the legacy query-token path.
- Bounded body helper returns/raises 413 when `Content-Length` exceeds `ServeSettings.max_request_body_bytes`.
- `_json_response()` does not emit wildcard CORS for session/OIDC auth and only echoes configured allowed origins.
- OIDC callback `Set-Cookie` includes `Secure` when TLS is enabled.
- Redacted thread response omits command output and file content previews by default.

New tests in `tests/test_store_scalability.py`:
- `append_item()` does not rewrite the full JSONL file merely to update metadata.
- `list_thread_meta()` reads sidecar metadata when present.
- prefix matching uses metadata-only listing before loading the full thread.
- concurrent append simulation with two store instances does not corrupt JSONL.
- atomic rewrite leaves either the old valid file or new valid file if an injected write failure occurs.

Existing tests likely needing updates:
- `tests/test_config.py` or related config default tests for approval mode expectations.
- `tests/test_default_launch.py` if it assumes auto approval by default.
- `tests/test_serve*` files if they assume query tokens are accepted without explicit setting.
- `tests/test_threads*` or picker tests if metadata sidecars change ordering/timestamps.

Validation commands:
```powershell
cd agent-cli
pytest tests/test_execution_hardening.py tests/test_serve_hardening.py tests/test_store_scalability.py -q
pytest tests/test_config.py tests/test_auth_policy.py tests/test_auth_webhooks.py tests/test_approval.py tests/test_store.py -q
pytest -q
```

[Implementation Order]
The implementation should proceed from low-level helpers and settings to call-site integration, then tests and documentation.

1. Add and parse new settings in `agent/settings.py`, including execution hardening, Docker hardening, serve body/auth/CORS settings, and any store settings if centralized there.
2. Update `agent/config.py` to default to interactive approval and preserve explicit auto-approval behavior.
3. Harden Docker argv construction in `agent/execution/docker.py` and add local execution metadata in `agent/execution/local.py`.
4. Add `agent/serve/request_limits.py` and replace direct `Content-Length` reads in auth, thread, and IDE routes.
5. Update `agent/serve/auth.py` and `agent/serve/routes/auth_routes.py` so query tokens are disabled by default and secure cookies are used with TLS.
6. Update `agent/serve/http_response.py` and serve route call sites to use explicit CORS, redacted thread serialization, and metadata-only prefix lookup.
7. Refactor `agent/store.py` with metadata sidecars, atomic writes, and lock-file protection while preserving JSONL compatibility.
8. Update serve/dashboard/thread listing code to prefer `list_thread_meta()` where full turn data is not required.
9. Add new regression tests for execution, serve hardening, and store scalability/concurrency.
10. Update existing tests whose assumptions changed, especially approval defaults and query-token behavior.
11. Update `README.md` to document safe defaults, hardening settings, and compatibility opt-ins.
12. Run focused tests, then the full test suite, and fix regressions.