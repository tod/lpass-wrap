---
name: lpass-wrap-usage
description: Use the lpass-wrap library when writing Python that reads or writes LastPass secrets. Use when writing any script that uses LastPass or the lpass CLI, migrating raw subprocess lpass calls, storing or retrieving secrets in Python, or reviewing code that shells out to lpass.
---

Prefer `lpass_wrap.LpassClient` over raw `subprocess` calls to the `lpass` CLI. The library handles stdin-based secret passing (so tokens never appear in process listings), consistent error types, and sync flags.

If `from lpass_wrap import LpassClient` fails, ask the user where the library is installed or how to add it to the current venv before proceeding.

## Standard pattern

```python
from lpass_wrap import LpassClient, LpassItemNotFoundError

_lpass = LpassClient(username="you@example.com")

def main() -> None:
    _lpass.ensure_login()          # prompts if needed; raises in non-TTY
    pw = _lpass.get_password("Homelab/My Secret")
    _lpass.upsert("Homelab/My Secret", username="svc", password=new_value)
```

## Key methods

| Method | What it does |
|---|---|
| `ensure_login()` | Logs in interactively if needed; raises `LpassNotLoggedInError` in non-TTY |
| `item_exists(name)` | Returns bool; uses `--sync=no` (fast, but stale — avoid for post-write checks) |
| `get_password(name)` | Returns password field; raises `LpassItemNotFoundError` if missing |
| `get_username(name)` | Returns username field |
| `get_item(name)` | Returns `LpassItem`; forces `--sync=now`; raises `LpassMultipleMatchesError` if duplicates exist |
| `upsert(name, username, password)` | Create or update — uses `get_item()` internally so it is sync-authoritative |
| `create(name, username, password)` | Explicit create |
| `update(name, username, password)` | Explicit update; **silently creates** if name not found (prefer `upsert`) |
| `pending_sync_count()` | Number of items in `upload-queue/` — written locally but not yet pushed |
| `failed_sync_count()` | Number of items in `upload-fail/` — permanently failed after 5 retries (kept 14 days) |
| `assert_sync_clean()` | Raises `LpassSyncError` if any items are pending or permanently failed; call after all writes |

## Exceptions

```python
from lpass_wrap import LpassItemNotFoundError, LpassMultipleMatchesError, LpassNotLoggedInError, LpassCommandError, LpassSyncError
```

- `LpassItemNotFoundError` — item name doesn't exist; catch this instead of checking `item_exists()` first when you expect the item to be there.
- `LpassMultipleMatchesError` — raised by `get_item()` (and therefore `upsert()`) when duplicate items share the same name. Has `.item_name` and `.count`. Use `lpass ls` and `lpass rm <UNIQUEID>` to clean up.
- `LpassNotLoggedInError` — raised by `ensure_login()` in non-TTY sessions; let it propagate.
- `LpassCommandError` — underlying `lpass` command failed; has `.returncode` and `.stderr`.
- `LpassSyncError` — raised by `assert_sync_clean()` when writes have not reached the server. Has `.pending` and `.failed` counts.

## Verifying sync in automation scripts

`--sync=now` enqueues the upload and starts a background process to push it — the calling script can exit before the upload completes. Always call `assert_sync_clean()` after writes:

```python
_lpass.upsert("Homelab/My Secret", username="svc", password=new_value)
_lpass.assert_sync_clean()  # raises LpassSyncError if pending or failed
```

Catch `LpassSyncError` in `main()` alongside the other lpass exceptions. It
subclasses `LpassError`, **not** `RuntimeError` — an `except RuntimeError`
will not catch it.

### Ansible pattern

Use a task (not a handler — this is a post-condition check, not a change response):

```yaml
- name: Verify LastPass sync completed
  ansible.builtin.command:
    argv:
      - python3
      - -c
      - |
        from lpass_wrap import LpassClient, LpassSyncError
        import sys
        c = LpassClient("you@example.com")
        try:
            c.assert_sync_clean()
        except LpassSyncError as e:
            sys.exit(str(e))
  changed_when: false
```

Place it at the end of any play that writes secrets, or inside a `block`/`always` so it runs even if earlier tasks fail.

## Gotchas

**`upsert` is the right default.** Scripts that run repeatedly should use `upsert`, not conditional `create`/`update`. It uses `get_item()` internally (authoritative `--sync=now`) so it also surfaces `LpassMultipleMatchesError` if duplicates exist.

**`get_item()` forces a sync; `item_exists()` does not.** `item_exists()` reads the local cache only (`--sync=no`) and may return False for an item that's in the upload queue. Use `get_item()` whenever you need a freshly-written item to be visible.

**Don't mix lpass-wrap and raw subprocess lpass calls in the same script.** They use different sync strategies and you'll get inconsistent cache behavior.
