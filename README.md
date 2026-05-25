# agent-cli

A local coding agent CLI inspired by OpenAI Codex — powered by [OpenRouter](https://openrouter.ai).

## Install

```powershell
cd agent-cli
pip install -e ".[dev]"
pip install -e ".[tui]"    # optional: interactive terminal UI
```

## Configure

Environment + `~/.agent-cli/config.toml` (CLI flags → env → file → defaults).

```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-..."
agent config show
agent doctor
agent doctor --deep   # optional MCP startup check
```

### Example `~/.agent-cli/config.toml`

```toml
model = "anthropic/claude-sonnet-4"
approval_mode = "interactive"
max_tool_rounds = 25
sandbox_mode = "workspace-write"   # danger-full-access | read-only | workspace-write
exec_policy = "untrusted"          # prompt | untrusted | never

[exec_policy_rules]
allow = [
  "git status*",
  "git diff*",
  "pytest*",
  "python -m pytest*",
  "rg *",
  "dir",
  "dir *",
]
deny = [
  "rm -rf *",
  "del /s *",
  "format *",
]

[compaction]
enabled = true
keep_recent_turns = 2
threshold = 0.7
summary_max_chars = 8000

[openrouter]
max_retries = 3
retry_base_delay_sec = 1.0
request_timeout_sec = 120

[recording]
enabled = true
keep_last_runs_per_thread = 50

[isolation]
enabled = true
strip_env = true
kill_process_tree_on_timeout = true
clear_network_env_hints = true

[web_search]
enabled = false
provider = "duckduckgo"
max_results = 5
timeout_sec = 15

[skills]
max_active = 3
max_body_chars = 4000
enable_user_skills = true
enable_project_skills = true
project_rules_max_chars = 8000

[mcp_servers.filesystem]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-filesystem", "E:/lampcode/agent-cli/examples/demo-project"]
enabled = true
require_approval = false

[mcp]
startup_timeout_sec = 30
tool_name_prefix = true
tool_call_timeout_sec = 60
```

Project-local MCP override: `{cwd}/.agent-cli/mcp.toml` (wins on name collision).

## Usage

```powershell
agent run "Find why tests fail and fix them" --cwd e:\lampcode\agent-cli\examples\demo-project
```

### Key flags

| Flag | Description |
|------|-------------|
| `--auto-approve` | Skip all approval prompts |
| `--session-auto-approve` | Session-wide auto-approve (thread run) |
| `--sandbox` | `danger-full-access` (default), `read-only`, or `workspace-write` |
| `--title` | Set thread title when creating a new thread |
| `--resume-last` | Resume latest thread for cwd |
| `--jsonl-events` | Machine-readable event stream |
| `--show-system-prompt` | Debug: print system prompt |
| `--quiet-tools` | Hide tool lines |
| `--execution-backend` | `local` (default) or `docker` for `run_command` |
| `--docker-image` | Override Docker image when using docker backend |
| `--sync` | SSH sync mode override: `push`, `push-pull`, or `manual` |
| `--force-sync` | Bypass `max_upload_mb` size guard |
| `--resume-multi-agent` | Resume supervisor turn from latest checkpoint |
| `--retry-failed` | Retry failed workers when resuming checkpoint |

### Approval keys

- `y` — approve once
- `n` — deny (default)
- `a` — approve all for this **turn**
- `A` — approve all for this **session**

## Sandbox modes (Phase 4)

Heuristic policy checks before shell/MCP/file tools run — **not OS-level isolation**.

| Mode | Behavior |
|------|----------|
| `danger-full-access` | Default; same as pre-Phase 4 |
| `read-only` | Blocks write-like and network-like shell commands, file writes, patches, mutating MCP tools |
| `workspace-write` | Writes allowed only under thread `cwd`; blocks network-like commands |

```powershell
# Block file creation in read-only mode
agent run "create file foo.txt" --cwd e:\lampcode\agent-cli\examples\demo-project --sandbox read-only

# Allow writes under cwd only
agent run "fix tests" --cwd e:\lampcode\agent-cli\examples\demo-project --sandbox workspace-write
```

CLI `--sandbox` overrides `sandbox_mode` in config. Denied commands emit `sandbox.blocked` events and persist as `commandExecution` with `status=denied`.

**Limitations:** redirect/filename parsing is best-effort; sub-shells, scripts, and indirect writes are not fully contained.

## Exec policy

Auto-approve safe read-only commands when `exec_policy = "untrusted"`:

```powershell
agent exec-policy test "git status --short"
agent exec-policy test "del /s /q foo"
```

- `prompt` — always prompt (plus turn/session approvals)
- `untrusted` — auto-approve allow-list + read/test commands; prompt for the rest
- `never` — never prompt (intended for CI with `--auto-approve`; `agent doctor` warns)

## Thread fork & titles

```powershell
agent threads list
agent threads fork abc123 --title "experiment"
agent run "try alternate fix" --thread-id <forked-id>
agent threads rename abc123 "fix calc bug"
agent run "fix it" --title "calc bug"   # title on new thread only
```

Forked threads store `forked_from` in JSONL metadata; `agent threads list` shows lineage.

## Interactive TUI (Phase 5)

```powershell
pip install -e ".[tui]"
agent tui --cwd e:\lampcode\agent-cli\examples\demo-project
agent tui --resume-last
```

Layout: thread list (left), live transcript (right), status bar, input at bottom.

| Key | Action |
|-----|--------|
| Enter | Submit turn (or approval key when pending) |
| `y` / `n` / `a` / `A` | Approve / deny / turn / session (inline during approvals) |
| Ctrl+C | Cancel running turn |
| Ctrl+L | Clear input |
| `q` | Quit (warns if turn running) |

Headless `agent run` is unchanged — TUI is optional.

## Run recording & replay

Every turn writes `~/.agent-cli/runs/{thread_id}/{turn_id}.jsonl` (one `AgentEvent` per line).

```powershell
agent runs list
agent runs list --thread-id abc123
agent runs show turn-id-prefix --human
agent runs replay turn-id-prefix
```

Use `--jsonl-events` on `agent run` for stdout streaming; run logs mirror the same event schema.

## Subprocess isolation (Phase 5)

When `sandbox_mode` is not `danger-full-access` and `[isolation] enabled = true`:

- Subprocess cwd locked to thread cwd
- Environment stripped to allowlist (+ proxy vars removed)
- Timeout kills process tree (`taskkill /T /F` on Windows)

Emits `isolation.applied` events. **Not** true network/filesystem isolation — env hardening only.

## Web search (optional, off by default)

```toml
[web_search]
enabled = true
```

Requires approval in interactive mode; blocked in `read-only` sandbox. Uses DuckDuckGo (no API key). Results persist as `webSearch` items.

```powershell
# Enable in config, then ask the agent to search
agent run "look up pytest exit code 1 docs" --cwd examples/demo-project
```

## Skills

Skills live in folders with `SKILL.md`:

```
{cwd}/.agent-cli/skills/my-skill/SKILL.md
{cwd}/skills/my-skill/SKILL.md
~/.agent-cli/skills/my-skill/SKILL.md
```

```powershell
agent skills list --cwd e:\lampcode\agent-cli\examples\demo-project
agent skills show pytest-fix --cwd e:\lampcode\agent-cli\examples\demo-project
```

Skills are auto-selected by keyword overlap or `@skill-name` mentions (max 3 per turn).

## MCP

```powershell
agent mcp list
agent mcp tools --cwd e:\lampcode\agent-cli\examples\demo-project
```

MCP tools are exposed as `mcp__{server}__{tool}` and persisted as `mcpToolCall` items.

## Built-in tools

`read_file`, `search_repo`, `apply_patch`, `write_file`, `run_command`, optional `web_search` + MCP tools.

## Project rules

Loads `AGENTS.md`, `agents.md`, or `.agents/AGENTS.md` into `# Project Rules` in the system prompt.

## Phase 6 — Docker execution + multi-agent supervisor

### Execution backends

Commands from `run_command` can run on the host (`local`, default) or inside Docker (`docker`).

```toml
[execution]
backend = "local"          # local | docker | ssh
default_image = "python:3.12-slim"
workspace_mount = "/workspace"
network = "none"           # none | bridge (bridge needs approval first use)
memory_limit = "1g"
cpu_limit = "1.0"
command_timeout_sec = 120
auto_pull = false

[execution.docker]
binary = "docker"
platform = ""
```

Precedence: `--execution-backend` → `AGENT_EXECUTION_BACKEND` → config → `local`.

```powershell
# Host (default)
agent run "pytest -q" --cwd e:\lampcode\agent-cli\examples\demo-project

# Docker (requires Docker Desktop on Windows)
agent run "python -c `"print(1+1)`"" `
  --execution-backend docker `
  --docker-image python:3.12-slim `
  --cwd e:\lampcode\agent-cli\examples\demo-project

agent doctor --deep   # checks docker binary + hello-world (skip in CI via AGENT_SKIP_DOCKER_INTEGRATION=1)
```

Docker mounts thread `cwd` at `/workspace` (read-only when sandbox is `read-only` and command is read-like). Default network is **none** for safety. `commandExecution` items record `backend`, `container_id`, and `image` when applicable.

### Multi-agent supervisor (v1 → v2 in Phase 7)

Phase 6 introduced synchronous `spawn_worker`. Phase 7 adds async queueing, `wait_workers`, `list_workers`, and depth limits — see **Phase 7** below.

### Export & read-only viewer

```powershell
agent threads export abc123 --out thread.md
agent runs export turn-id-prefix --out run.md

agent serve --host 127.0.0.1 --port 8765
# GET http://127.0.0.1:8765/threads
# GET http://127.0.0.1:8765/threads/{id}
# GET http://127.0.0.1:8765/runs/{turn_id}
```

Binding to `0.0.0.0` prints a warning — localhost only by default.

## Phase 7 — SSH remote backend + multi-agent v2

### SSH execution backend

Run `run_command` on a remote host via OpenSSH (`ssh.exe` on Windows).

```toml
[execution]
backend = "ssh"

[execution.ssh]
host = "devbox.local"
user = "ubuntu"
port = 22
identity_file = "~/.ssh/id_ed25519"
known_hosts = "~/.ssh/known_hosts"
remote_workspace = "/home/ubuntu/workspace"
connect_timeout_sec = 15
command_timeout_sec = 120
strict_host_key_checking = true

# [execution.ssh.jump]
# host = "bastion.example.com"
# user = "jumpuser"
```

Env overrides: `AGENT_SSH_HOST`, `AGENT_SSH_USER`, `AGENT_SSH_IDENTITY_FILE`.

**v1 limitation (pre-Phase 8):** no automatic workspace sync — ensure `remote_workspace` already contains your repo. Phase 8 adds rsync/scp sync — see below.

```powershell
agent run "pytest -q" `
  --execution-backend ssh `
  --ssh-host devbox.local `
  --ssh-user ubuntu `
  --cwd e:\lampcode\agent-cli\examples\demo-project

agent execution test --backend ssh --cmd "uname -a"
agent execution test --backend local --cmd "echo ok"
agent doctor --deep   # checks ssh binary + config completeness
```

First SSH command per thread requires approval unless `--session-auto-approve`. `commandExecution` items record `backend=ssh`, `remote_host`, `remote_user`.

### Multi-agent orchestration v2

Supervisor tools when `--multi-agent` or `[multi_agent] enabled = true`:

| Tool | Purpose |
|------|---------|
| `spawn_worker` | Queue async worker (returns `worker_id`) |
| `wait_workers` | Block until workers complete; JSON summaries |
| `list_workers` | Pending/running/completed workers this turn |

```toml
[multi_agent]
enabled = true
max_workers_per_turn = 5
max_worker_depth = 2
max_concurrent_workers = 3
worker_auto_approve = false
inherit_execution_backend = true
wait_timeout_sec = 600
allow_worker_spawn = true
```

```powershell
agent run "Split refactor and tests across workers, then wait" `
  --multi-agent `
  --cwd e:\lampcode\agent-cli\examples\demo-project
```

Workers persist as `collabWorker` items with `worker_id`, `depth`, and status (`queued|running|completed|failed|timed_out`).

### Docker file tools (opt-in)

When `execution.backend=docker` and file tools opt-in is enabled, `write_file` / `apply_patch` write through the mounted workspace (visible inside the container):

```toml
[execution.docker]
file_tools_in_container = true
```

### Export & serve (HTML)

```powershell
agent threads export abc123 --format html --out thread.html
agent serve --port 8765
# http://127.0.0.1:8765/          HTML thread list
# http://127.0.0.1:8765/threads/{id}.html
```

## Phase 8 — SSH workspace sync, session pooling, supervisor checkpoints

### SSH workspace sync

Push local thread `cwd` to `remote_workspace` before remote turns; optionally pull changes back after a successful turn.

```toml
[execution]
backend = "ssh"

[execution.ssh]
host = "devbox.local"
user = "ubuntu"
remote_workspace = "/home/ubuntu/workspace"
sync_enabled = true
sync_mode = "push-pull"          # push | push-pull | manual
sync_on = "turn_start"
pull_on_turn_end = true
delete_remote_extra = false      # rsync --delete (dangerous)

[execution.ssh.sync]
transport = "auto"               # auto | rsync | scp
exclude = [".git/objects", "__pycache__", ".venv", "node_modules", ".agent-cli"]
include_dotfiles = false
max_upload_mb = 200
checksum = "mtime"
```

```powershell
# Manual sync
agent sync push --cwd . --ssh-host devbox.local --ssh-user ubuntu
agent sync pull --cwd . --ssh-host devbox.local --ssh-user ubuntu
agent sync status --ssh-host devbox.local

# Turn with push-pull
agent run "pytest -q" `
  --execution-backend ssh `
  --sync push-pull `
  --ssh-host devbox.local `
  --ssh-user ubuntu `
  --cwd e:\lampcode\agent-cli\examples\demo-project

# Override size guard (default 200 MB)
agent run "..." --execution-backend ssh --sync push --force-sync
```

**Safety:** first sync push per thread requires approval (like first SSH command). Uploads over `max_upload_mb` are blocked unless `--force-sync`. Sync never leaves thread `cwd`.

**Windows notes:** uses `ssh.exe` / `scp.exe` from OpenSSH. `rsync` is used when available (`auto` transport) — install via WSL, Git Bash, or Cygwin for faster incremental sync. If only `scp` is available, full-tree copy is used.

**Push-pull warning:** pull may overwrite local uncommitted changes. Commit or stash before `push-pull` turns.

Events: `execution.sync.started`, `execution.sync.completed`, `execution.sync.failed`. Persisted as `workspaceSync` turn items.

### SSH session pooling

Reuse OpenSSH ControlMaster connections to reduce handshake overhead.

```toml
[execution.ssh.pool]
enabled = true
max_sessions = 3
idle_timeout_sec = 300
healthcheck_cmd = "echo ok"
```

Control sockets live under `~/.agent-cli/ssh-sockets/`. Windows OpenSSH support for ControlMaster varies — the pool tries ControlMaster and falls back to one-shot `ssh` with a one-time log if unsupported.

### Supervisor checkpoints

Resume multi-agent supervisor turns after cancel or crash.

```toml
[multi_agent]
enabled = true
checkpoint_enabled = true
checkpoint_dir = "~/.agent-cli/checkpoints"
```

Checkpoints: `~/.agent-cli/checkpoints/{thread_id}/{turn_id}.json` (worker registry snapshot + messages).

```powershell
agent multi-agent status --thread-id abc123
agent multi-agent resume --thread-id abc123
agent multi-agent resume --thread-id abc123 --turn-id turn456 --retry-failed

agent run "..." --multi-agent --resume-multi-agent --thread-id abc123
```

Completed workers are skipped on resume; queued/running/failed workers continue (failed only with `--retry-failed`).

### Doctor (Phase 8)

```powershell
agent doctor --deep
```

Reports `rsync`/`scp` availability, sync config + estimated cwd size, SSH pool socket dir, and checkpoint dir writability.

## Phase 9 — Incremental sync v2, authenticated serve, supervisor v3

### Incremental sync v2

Manifest-based delta sync with conflict detection. Uses `~/.agent-cli/sync-state/{thread_id}.json` plus `.agent-cli/sync-manifest.json` in cwd.

```toml
[execution.ssh.sync]
mode = "incremental"              # full | incremental
conflict_strategy = "prompt"      # prompt | local-wins | remote-wins | abort
hash_on_conflict = true
manifest_path = ".agent-cli/sync-manifest.json"
max_files_per_sync = 5000
```

**Backward compatible:** without a manifest file on disk, sync falls back to Phase 8 full-tree push/pull.

```powershell
agent sync plan --cwd . --ssh-host devbox.local
agent sync push --incremental
agent sync pull --incremental
agent sync resolve --path src/foo.py --strategy local-wins
```

Conflict prompts: `l` local-wins, `r` remote-wins, `s` skip, `a` abort (CLI/TUI). Emits `execution.sync.plan` before transfer.

**Warning:** conflicts reduce risk but pull can still overwrite local uncommitted work.

### Authenticated serve v2

```toml
[serve]
host = "127.0.0.1"
port = 8765
auth_token = ""                   # auto-generated at startup if empty
allow_remote_bind = false
enable_control = true             # cancel only in Phase 9
cors = false
```

```powershell
agent serve --token my-secret --port 8765
agent serve --no-control          # read-only like Phase 8
```

API (Bearer token or `?token=`):
- `GET /threads/{id}/events` — SSE stream of latest run events
- `GET /metrics` — JSON counters/gauges
- `POST /threads/{id}/cancel` — cancel active turn (when control enabled)

Refuses `0.0.0.0` unless `--allow-remote-bind`. No HTTP turn **start** in Phase 9.

### Supervisor v3

```toml
[multi_agent]
retry_failed_workers = true
retry_backoff_sec = 5
retry_max_attempts = 2
checkpoint_compact_after_workers = 10
metrics_enabled = true
```

- Failed worker retry with exponential backoff on `multi-agent resume --retry-failed`
- Checkpoint compaction archives older snapshots under `{turn}.archive/`
- `agent metrics show` prints JSON runtime counters

## Phase 10 — Remote manifest sync, HTTP turn control, production ops (v1.0.0)

### Live remote manifest + sync-state v3

Before incremental push/pull when `backend=ssh`, the agent fetches the remote manifest over SSH and merges it with local manifest + per-thread sync-state.

```toml
[execution.ssh.sync]
fetch_remote_manifest = true
remote_scan_max_files = 5000
replicate_remote_state = true
remote_shell = "bash -lc"
on_remote_manifest_missing = "create"   # create | empty | abort
```

```powershell
agent sync fetch-remote --ssh-host devbox.local --ssh-user ubuntu
agent sync plan --verbose --ssh-host devbox.local
agent sync push --incremental
agent sync pull --incremental
```

Events: `execution.sync.remote_manifest_fetched`, `execution.sync.remote_scan_completed`. Replicated snapshot: `~/.agent-cli/sync-state/{thread_id}.remote.json`.

### Serve v3 — HTTP turn start + approvals

```toml
[serve]
enable_control = true
enable_turn_start = true
max_concurrent_turns = 2
approval_timeout_sec = 300
stream_buffer_size = 256
auth_token = "your-secret-token"
```

```powershell
# Terminal 1 — dashboard with turn control
agent serve --enable-turn-start --token my-secret --max-concurrent-turns 2

# Terminal 2 — start a turn
$headers = @{ Authorization = "Bearer my-secret"; "Content-Type" = "application/json" }
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8765/threads/{thread_id}/run" `
  -Headers $headers -Body '{"prompt":"Fix failing tests","auto_approve":false}'

# Approve a pending tool
Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8765/approvals/{approval_id}" `
  -Headers $headers -Body '{"decision":"accept"}'
```

API (Bearer required):
- `POST /threads/{id}/run` — start harness turn (background thread)
- `GET /threads/{id}/events?turn_id=...` — SSE including `approval.requested` with `approval_id`
- `POST /approvals/{approval_id}` — `accept` | `deny` | `accept_turn` | `accept_session`
- `POST /threads/{id}/cancel` — cancel active turn
- `GET /metrics/prometheus` — Prometheus text exposition

When `enable_turn_start=true`, `/` serves an interactive dashboard (Run + Approve/Deny + SSE transcript).

### Production ops

```powershell
$env:AGENT_LOG_FORMAT = "json"    # structured JSON logs to stderr
agent config validate --strict
agent version
agent metrics show --format prometheus
```

Prometheus metrics include `agent_turns_total`, `agent_tool_calls_total`, `agent_sync_bytes_total`, `agent_sync_conflicts_total`, `agent_workers_total`, `agent_http_turns_active`.

Supervisor polish: checkpoint JSON includes `worker_dependencies` (structure for Phase 11); `agent multi-agent status --verbose` shows attempts, backoff, last error.

## Production checklist

1. Set `OPENROUTER_API_KEY` and a fixed `serve.auth_token` (never bind remotely without one).
2. Keep `serve.host = "127.0.0.1"` unless you explicitly need remote access; use `--allow-remote-bind` only with firewall + token.
3. Enable `enable_turn_start` only when needed; cap `max_concurrent_turns`.
4. Run `agent config validate --strict` in CI/deploy scripts.
5. Back up `~/.agent-cli/sync-state/` before aggressive `push-pull` sync on shared remotes.
6. Run `pytest` (580+ tests) before release; check `agent doctor --deep` for TLS/RBAC/kernel/OIDC/IDE/keyring/marketplace/budgets.

## Phase 14 — Web IDE lite + enterprise auth + cross-turn DAG v5 (v1.4.0)

### Web IDE lite (serve)

Authenticated file tree + Monaco editor for thread cwd. RBAC: viewer read-only, operator/admin can save.

```toml
[serve.ide]
enabled = true
max_file_bytes = 1048576
max_tree_entries = 2000
monaco_cdn = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.0/min/vs"
```

API: `GET /ide/tree`, `GET /ide/file`, `PUT /ide/file`, `GET /ide/diff?thread_id=&path=`

```powershell
agent serve --enable-turn-start --enable-ide
```

### mTLS (optional)

```toml
[serve.tls]
enabled = true
require_client_cert = true
client_ca_file = "~/.agent-cli/certs/client-ca.pem"
```

Document reverse-proxy termination (nginx/Traefik) as production alternative.

### OAuth device code (headless CI)

```toml
[serve.auth.oidc]
device_code_enabled = true
device_client_id = "..."    # optional separate public client
```

```powershell
agent auth login --device
agent auth login --device --headless --timeout 600
```

Tokens stored in `~/.agent-cli/auth/oidc.json` (use keyring when available).

### Cross-turn DAG v5

Worker graphs persist across turns in `~/.agent-cli/dag-state/{thread_id}.json`.

```toml
[multi_agent]
dag_enabled = true
dag_persist_across_turns = true
dag_max_age_sec = 86400
dag_auto_resume = false
```

```powershell
agent multi-agent dag-status --thread-id <id>
agent multi-agent dag-clear --thread-id <id> --yes
agent multi-agent resume --thread-id <id>
```

Events: `multi_agent.dag.persisted`, `multi_agent.dag.resumed_across_turns`.

Metrics: `agent_ide_requests_total`, `agent_auth_device_code_total`, `agent_dag_persist_total`.

## Phase 15 — OAuth rotation + marketplace CDN + budgeted swarms (v1.5.0)

### OAuth refresh rotation + secure storage

Keyring-first token storage with file fallback (`~/.agent-cli/auth/oidc.json`, mode 0600 on Unix).

```toml
[serve.auth.oidc]
refresh_rotation = true
refresh_skew_sec = 300
prefer_keyring = true

[auth.storage]
backend = "auto"                  # auto | keyring | file
service_name = "agent-cli"
```

```powershell
agent auth login --device
agent auth login --device --headless --timeout 600
agent auth status
agent auth refresh --force
agent auth logout
```

### Remote marketplace CDN + revocations

```toml
[skills.marketplace]
remote_registry_url = "https://skills.example.com/registry.json"
remote_registry_signature_key = "publisher1"
revocation_list_url = "https://skills.example.com/revocations.json"
```

```powershell
agent skills marketplace sync --force
agent skills marketplace list --remote
agent skills install docs-helper@1.0.0 --from-registry
agent skills revocations check
agent skills lock update
agent skills lock verify
```

### Budgeted swarms v1

```toml
[multi_agent.budgets]
enabled = true
max_workers_spawned = 20
on_budget_exceeded = "kill"
```

```powershell
agent run "coordinate workers" --multi-agent --budget-profile strict
agent multi-agent budgets show --thread-id <id>
```

Metrics: `agent_auth_refresh_total`, `agent_marketplace_sync_total`, `agent_swarm_budget_exceeded_total`.

## Phase 16 — AppContainer + IDE v2 + cross-thread DAG (v1.6.0)

### Windows AppContainer sandbox (experimental)

Real isolation path for local `run_command` on Windows 10+ with fallback to restricted token.

```toml
[sandbox.kernel]
enabled = true
backend = "auto"
fail_open = true

[sandbox.kernel.windows]
backend_preference = "appcontainer_then_restricted"
allow_network = false
workspace_cap = true
```

```powershell
agent doctor   # shows AppContainer: available|unavailable
```

**Not malware-grade isolation** — complement with budgets, approvals, and RBAC.

### IDE v2 (multi-tab + diff gutter)

```toml
[serve.ide]
enabled = true
max_open_tabs = 10
show_diff_gutter = true
autosave = false
```

API: `GET /ide/history?path=...`, `GET /ide/tabs/state` (plus existing `/ide/tree`, `/ide/file`, `/ide/diff`).

```powershell
agent serve --enable-ide --enable-turn-start
```

### Cross-thread program DAG

```toml
[multi_agent.cross_thread]
enabled = false
program_id_auto = true
state_dir = "~/.agent-cli/programs"
max_threads_linked = 20
```

```powershell
agent programs list
agent programs show <program-id>
agent programs link-thread --program-id <id> --thread-id <id>
agent multi-agent graph --program-id <id>
agent programs clear <program-id> --yes
```

Use `spawn_worker` with `"program_scope": true` when cross-thread is enabled.

## Phase 17 — OAuth policy + scheduled swarms + IDE diagnostics (v1.7.0)

### OAuth policy engine (off by default)

Declarative rules evaluated at OIDC/device login and on control endpoints (`thread.run`, approvals, IDE PUT).

```toml
[serve.auth.policy]
enabled = false
require_https = true
introspection_url = ""             # optional RFC7662 endpoint
introspection_client_id = ""
introspection_client_secret = ""
revoke_on_role_change = true
max_session_age_sec = 28800
idle_timeout_sec = 3600
step_up_for_control_actions = true

[[serve.auth.policy.rules]]
name = "require-mfa-claim"
match_roles = ["operator", "admin"]
require_claims = { "amr" = "mfa" }
deny_message = "MFA required for operator actions"
```

```powershell
agent auth policy test --role operator --action thread.run
agent auth sessions revoke-all --yes
agent serve policy status
```

Revoked sessions (hashed in `~/.agent-cli/auth/revoked-sessions.json`) fail immediately with 401.

### Scheduled swarms (budget-gated, off by default)

No daemon — use Windows Task Scheduler to invoke `agent schedule tick` every minute.

```toml
[schedule]
enabled = false
require_budgets = true
require_multi_agent = true
default_approval_mode = "interactive"
allow_unattended_auto = false
state_file = "~/.agent-cli/schedules.json"
```

```powershell
agent schedule add nightly-tests --cron "0 2 * * *" --cwd E:/lampcode/agent-cli/examples/demo-project --prompt "Run pytest"
agent schedule tick
agent schedule run nightly-tests --now
agent schedule history --job-id nightly-tests
```

**Task Scheduler (PowerShell XML snippet):**

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><CalendarTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary>
    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    <Repetition><Interval>PT1M</Interval></Repetition></CalendarTrigger></Triggers>
  <Actions><Exec>
    <Command>agent</Command>
    <Arguments>schedule tick</Arguments>
    <WorkingDirectory>E:\lampcode\agent-cli</WorkingDirectory>
  </Exec></Actions>
</Task>
```

**Unattended risk:** `default_approval_mode=auto` is blocked unless `allow_unattended_auto=true` **and** `~/.agent-cli/schedule-unattended-acknowledged` exists.

### IDE v3 — LSP-style diagnostics (lightweight)

Subprocess analyzers (py_compile / ruff / eslint) — no full language server.

```toml
[serve.ide.diagnostics]
enabled = true
timeout_sec = 10
python_tool = "auto"    # auto | py_compile | ruff | none
js_tool = "none"        # none | eslint
max_diagnostics = 200
run_on_open = true
```

```powershell
# With serve running and IDE enabled:
curl -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8765/ide/diagnostics?thread_id=THREAD&path=src/foo.py"
```

Monaco squiggles load automatically when opening files in the dashboard.

## Phase 18 — Program sync + CAE webhooks + schedule notifications (v1.8.0)

### Cross-machine program DAG sync

Replicate supervisor program state via git or S3-compatible storage.

```toml
[multi_agent.cross_thread]
enabled = true
sync_enabled = false
sync_backend = "git"                 # git | s3 | none
sync_interval_sec = 60
sign_program_state = true
signing_key_id = "program-sync"

[multi_agent.cross_thread.git]
repo_path = ".agent-cli/program-sync"
branch = "agent-programs"
remote = "origin"

[multi_agent.cross_thread.s3]
endpoint_url = "https://s3.amazonaws.com"
bucket = "my-agent-programs"
prefix = "programs/"
access_key_env = "AGENT_S3_ACCESS_KEY"
secret_key_env = "AGENT_S3_SECRET_KEY"
```

```powershell
agent programs sync push --program-id PROG_ID
agent programs sync pull --program-id PROG_ID
agent programs sync status
agent programs sync verify-signature PROG_ID
```

### OAuth CAE / role-change webhooks

```toml
[serve.auth.webhooks]
enabled = false
path = "/auth/webhooks/oidc-events"
shared_secret_env = "AGENT_WEBHOOK_SECRET"
revoke_on_events = ["role_changed", "session_revoked", "password_changed"]
revoke_all_subject_sessions = true
```

```powershell
$env:AGENT_WEBHOOK_SECRET = "your-secret"
agent auth webhooks test --event role_changed --subject user-123
agent serve webhooks status
```

### Schedule notifications + tick lock

```toml
[schedule.notifications]
enabled = false
webhook_url = "https://hooks.example.com/agent-cli"
webhook_secret_env = "AGENT_SCHEDULE_WEBHOOK_SECRET"
on_events = ["failed", "budget_exceeded", "completed"]
timeout_sec = 10
retry_count = 2
```

```powershell
agent schedule tick              # acquires ~/.agent-cli/schedule/tick.lock
agent schedule tick --force      # steal stale lock (admin)
agent schedule notifications test --event failed --job-id nightly-tests
```

**Task Scheduler note:** run `agent schedule tick` every minute; overlapping runs skip cleanly when lock is held.

## Tests

```powershell
pytest   # 750+ tests
```

## Phase 19 (planned, not implemented)

Full LSP bridge, Linux bubblewrap policy packs, distributed schedule leader election, native email/Slack integrations, real-time websocket program sync.

## Phase 13 — Kernel sandbox + OAuth/OIDC SSO (v1.3.0)

### Kernel sandbox (opt-in)

Real OS-level isolation for **local** `run_command`. Defaults off; Phase 11 profiles and heuristic sandbox unchanged when disabled.

```toml
[sandbox]
mode = "workspace-write"

[sandbox.kernel]
enabled = false
backend = "auto"                  # auto | bubblewrap | seatbelt | windows_restricted | none
fail_open = true
apply_to = ["run_command"]

[sandbox.kernel.linux]
bwrap_binary = "bwrap"
unshare_user = false
ro_bind_paths = ["/usr", "/lib", "/bin"]
allow_network = false

[sandbox.kernel.macos]
sandbox_exec = "/usr/bin/sandbox-exec"
profile_template = "workspace-write"

[sandbox.kernel.windows]
use_restricted_token = true
job_object_memory_mb = 1024
job_object_cpu_rate = 50
allow_network = false
```

| `sandbox.mode` | Kernel behavior |
|----------------|-----------------|
| `read-only` | Read-only FS view; deny writes/network (best-effort) |
| `workspace-write` | Writable cwd; read-only system paths |
| `danger-full-access` | Kernel sandbox disabled even if `kernel.enabled=true` |

Events: `sandbox.kernel.selected`, `sandbox.kernel.applied`, `sandbox.kernel.fallback`.

```powershell
# Enable kernel sandbox (Windows restricted token + Job Object limits)
agent config set sandbox.kernel.enabled true
agent doctor --deep   # kernel capability matrix per OS
```

**Platform limits (honest):**

- **Linux:** Requires `bubblewrap` installed; strongest option when available.
- **macOS:** Uses `sandbox-exec` profiles when on Darwin; skipped elsewhere.
- **Windows:** Restricted token + Job Object CPU/memory caps — stronger than none, **not** a malware-grade boundary (no AppContainer in v1.3.0).

### OAuth/OIDC SSO for serve

Enterprise login alongside bearer/session tokens. Break-glass local admin still works with `oidc+bearer`.

```toml
[serve]
auth_mode = "oidc+bearer"

[serve.tls]
enabled = true
cert_file = "~/.agent-cli/certs/server.crt"
key_file = "~/.agent-cli/certs/server.key"

[serve.auth.oidc]
issuer_url = "https://login.microsoftonline.com/<tenant-id>/v2.0"
client_id = "<app-client-id>"
client_secret = ""                # optional for PKCE public clients
redirect_uri = "https://127.0.0.1:8765/auth/oidc/callback"
scopes = ["openid", "profile", "email"]
pkce = true
session_ttl_sec = 28800

[serve.auth.oidc.role_mapping]
admin_groups = ["agent-admins"]
operator_groups = ["agent-operators"]
default_role = "viewer"
claim_groups_key = "groups"
claim_email_key = "email"
```

Endpoints: `GET /auth/oidc/login`, `GET /auth/oidc/callback`, `POST /auth/logout`. Dashboard shows **Sign in with SSO** when OIDC enabled.

```powershell
agent serve oidc configure --issuer "https://login.microsoftonline.com/<tenant>/v2.0" --client-id "<id>"
agent serve oidc test-login
agent auth sessions list
agent auth sessions revoke <session_id>
agent config validate --strict   # OIDC requires TLS
```

### Enterprise deployment guide

1. **Azure AD app registration:** Web redirect URI → `https://<host>:8765/auth/oidc/callback`. Enable ID tokens; add Microsoft Graph `GroupMember.Read.All` if using group claims (or configure app roles).
2. **TLS:** OIDC **requires** TLS — use `agent serve --tls --generate-self-signed` for dev, or terminate TLS at nginx/IIS/Caddy in production.
3. **Reverse proxy:** Forward `https://` to serve; set `redirect_uri` to the public HTTPS URL.
4. **Break-glass admin:** Keep `auth_mode = "oidc+bearer"` and a hashed local admin token for outages:

```powershell
agent auth hash-token "break-glass-secret"
# Add sha256 hash under [[serve.rbac.users]] with role = "admin"
```

5. **RBAC:** Enable `[serve.rbac]` when using OIDC so group→role mapping is enforced.
6. **Metrics:** `agent_sandbox_kernel_total{backend,result}`, `agent_auth_oidc_login_total{result}`.

## Tests

```powershell
pytest   # 530+ tests
```

## Phase 15 (planned, not implemented)

Windows AppContainer, remote marketplace CDN + revocation, budgeted autonomous swarms, multi-file IDE tabs/LSP/debugger, OAuth refresh rotation.

## Phase 12 — Serve TLS/RBAC, OTEL v2, signed skill marketplace (v1.2.0)

### Serve hardening

```toml
[serve]
auth_mode = "both"              # bearer | session | both
enable_turn_start = true

[serve.tls]
enabled = false
cert_file = "~/.agent-cli/certs/server.crt"
key_file = "~/.agent-cli/certs/server.key"

[serve.rbac]
enabled = false
default_role = "viewer"

[[serve.rbac.users]]
name = "alice"
token_hash = "sha256:..."
role = "admin"
```

Roles: **viewer** (read/SSE), **operator** (+ run/approve/cancel/sync), **admin** (+ user management).

```powershell
agent auth hash-token "my-secret"
agent serve users add bob "bob-secret" --role operator
agent serve --tls --generate-self-signed --enable-turn-start
```

Session login: `POST /auth/login` → `Authorization: Session <id>` or cookie.

### OTEL v2

Full spans: `sync.*`, `execution.*.run`, `approval.*`, `serve.http.request`. JSON logs include `trace_id`, `service.name`, `service.version`. RED histograms: `agent_turn_duration_seconds`, `agent_tool_duration_seconds`, `agent_http_request_duration_seconds`.

```powershell
agent telemetry status --verbose
agent metrics show --format prometheus
pip install -e ".[otel]"
```

### Signed skill marketplace

```powershell
pip install -e ".[marketplace]"
agent skills trust-key add publisher1 ~\.agent-cli\marketplace\keys\publisher1.ed25519.pub
agent skills install .\bundle.askill
agent skills verify docs-helper
agent skills marketplace list
```

`.askill` bundles: `SKILL.md`, `MANIFEST.json`, `SIGNATURE.ed25519`.
