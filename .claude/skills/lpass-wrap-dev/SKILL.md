---
name: lpass-wrap-dev
description: Reference for developing and debugging the lpass-wrap Python library. Use when editing lpass_wrap/ source files, writing tests, investigating lpass CLI behavior, or diagnosing sync failures or duplicate-entry bugs in the library.
---

## Key paths

| Path | Purpose |
|---|---|
| `lpass_wrap/client.py` | Core `LpassClient` class |
| `tests/test_client.py` | Unit tests (all subprocess calls mocked) |
| `$LPASS_HOME/upload-queue/` | One file per pending write not yet pushed to server |
| `$LPASS_HOME/blob` | Local encrypted cache read by `--sync=no` |
| `$LPASS_HOME/lpass.log` | CLI log (silent by default; see logging below) |

`$LPASS_HOME` defaults to `~/.lpass`; on this machine it is `~/.lpass_tod`.

## Running tests

The homelab root `.venv` does not have pytest — use the library's own venv:

```bash
cd python-libs/lpass-wrap
.venv/bin/pytest tests/ -v
```

## Enabling lpass CLI logging

Set `LPASS_LOG_LEVEL` to activate writing to `$LPASS_HOME/lpass.log`:

| Value | Level | What you get |
|---|---|---|
| 3 | ERROR | Errors only |
| 4 | WARNING | Warnings and errors |
| 6 | INFO | Request/response metadata |
| 7 | DEBUG | Full debug output |
| 8 | VERBOSE | Everything, including raw curl traffic |

```bash
LPASS_LOG_LEVEL=6 lpass show --sync=now "Homelab/My Secret"
```

**Warning:** `DEBUG` (7) and `VERBOSE` (8) can log session IDs and other secrets in the clear. Never share these logs without scrubbing first. `VERBOSE` produces the same raw curl output as the old log file — useful for one-off manual debugging, not for programmatic parsing.

Source: [lastpass-cli/log.h](https://github.com/lastpass/lastpass-cli/blob/master/log.h) · [lastpass-cli repo](https://github.com/lastpass/lastpass-cli)

## Sync architecture and gotchas

**`--sync=now` vs `--sync=no`**
`--sync=now` talks to the LastPass server; `--sync=no` reads only the local `blob`. All write operations in the library use `--sync=now`. `item_exists()` uses `--sync=no` for speed — do not rely on it immediately after a write.

**upload-queue and duplicate entries**
When `lpass add --sync=now` fails to reach the server (e.g. network issue), the item is queued locally but not in the server-authoritative blob. On the next session, `--sync=no` reads from blob and may not see the queued item, so a naive existence check calls `create()` again — producing a duplicate. LastPass does not enforce name uniqueness.

Check before diagnosing duplicate symptoms:
```python
n = client.pending_sync_count()  # non-zero means writes are stuck
```

**`upsert()` uses `get_item()`, not `item_exists()`, for the existence check**
`get_item()` forces `--sync=now` and is authoritative. `item_exists()` (`--sync=no`) is unreliable for items that may be in the upload queue. The `try/except LpassItemNotFoundError` pattern in `upsert()` is intentional — do not revert to the `item_exists()` branch.

**Two-command create/update pattern**
`lpass add --password` and `lpass edit --username` are issued separately because lpass field flags read a single value from stdin. There is no atomic single-command way to set both fields.

**Secrets always via stdin**
Never pass passwords or usernames as CLI arguments — they appear in process listings. Always use `_run_with_stdin()`.
