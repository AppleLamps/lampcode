# Harness roadmap (Phases 24–26)

Step-by-step implementation plans for solo harness work after **v2.6.0 / Phase 25**.

**North star:** Match Codex reliability mechanics (exec, sandbox, compaction, orchestration), then beat it on OpenRouter model choice, cost control, and CI/review scriptability.

| Phase | Target release | Theme | Plan |
|-------|----------------|-------|------|
| 24 | v2.5.0 | Reliability core | [phase-24.md](phase-24.md) |
| 25 | v2.6.0 | Daily UX | [phase-25.md](phase-25.md) |
| 26 | v2.7.0 | OpenRouter differentiation | [phase-26.md](phase-26.md) |

## Before starting any phase

```powershell
cd e:\lampcode\agent-cli
pytest -q                    # must be green (924+ passed, 1 skipped)
git log --oneline -5         # confirm Phase 23 commits on master
```

## Working rules (same as session context)

1. Implement in `lampcode/agent-cli` only — Codex repo is read-only reference.
2. One concern per commit; full `pytest -q` green before claiming a phase done.
3. Update [codex-comparison.md](../codex-comparison.md) when matching or intentionally skipping Codex behavior.
4. Keep [README.md](../../README.md) solo-first; enterprise stays in [enterprise.md](../enterprise.md).
5. Mock OpenRouter in tests; no flaky globals (approval state, DAG registries).

## Codex reference map

| Topic | Codex path |
|-------|------------|
| Tool orchestration | `codex-rs/core/src/tools/orchestrator.rs` |
| Unified exec | `codex-rs/core/src/tools/handlers/unified_exec/` |
| Sandbox | `codex-rs/core/src/tools/sandboxing.rs` |
| Compaction | `codex-rs/core/src/compact.rs` |
| Parallel tools | `codex-rs/core/src/tools/parallel.rs` |
| Exec CLI / JSONL | `codex-rs/exec/src/lib.rs` |
| Review | `codex-rs/exec` + `ReviewArgs` |

## Explicitly out of scope (all phases)

- ChatGPT OAuth / Plus billing
- Codex Cloud / plugin marketplace
- Multi-agent swarms / distributed scheduler (see enterprise.md)
- Code mode (V8), voice/realtime TUI
- Rewriting the harness in Rust
