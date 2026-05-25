# Run debug bundle (v1)

Local-only tarball for post-mortem debugging and sharing with teammates.

## Export

```powershell
agent runs export <turn-prefix> --format bundle --out run.bundle.tar.gz
agent runs bundle-info run.bundle.tar.gz
```

## Layout

| File | Contents |
|------|----------|
| `manifest.json` | Schema version, agent version, thread/turn IDs, model, cost, timestamp |
| `thread.jsonl` | Thread snapshot from the store |
| `events.jsonl` | Normalized v2 events for the turn |
| `config.redacted.json` | Resolved config with secrets stripped |

## Privacy

Bundles are intended for **local debugging**. Review before sharing:

- API keys are redacted (boolean flags only)
- Thread content may include file paths, command output, and user prompts
- Do not commit bundles to public repos without review
