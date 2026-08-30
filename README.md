# lpass-wrap

A Python wrapper around the [LastPass CLI](https://github.com/lastpass/lastpass-cli) (`lpass`).

Provides a clean, typed Python API for creating, reading, and updating LastPass
login items without exposing credentials in process argument lists or shell history.
Secrets are always passed to `lpass` via stdin.

## Requirements

* Python 3.10+
* [`lpass`](https://github.com/lastpass/lastpass-cli) installed and on `$PATH`
* An active LastPass account

## Installation

Not published on PyPI — install from the repository:

```bash
pip install git+https://github.com/tod/lpass-wrap
```

For development, clone and use [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/tod/lpass-wrap
cd lpass-wrap
uv sync
```

`uv sync` installs the dev tools (ruff, mypy, pyright, pytest) declared in
`[dependency-groups]`.  Note that `pip install -e ".[dev]"` does **not** work:
dev dependencies use [PEP 735](https://peps.python.org/pep-0735/) dependency
groups rather than extras, so pip warns `does not provide the extra 'dev'`,
installs the runtime dependencies only, and exits 0 — leaving you without a
test runner and no obvious error.

## Quick start

```python
from lpass_wrap import LpassClient

client = LpassClient(username="you@example.com")
client.ensure_login()  # prompts for master password if not already logged in

# Read an item
item = client.get_item("Homelab/My Secret")
print(item.username)  # "admin"
print(item.password)  # "**********" — redacted
print(item.password.get_secret_value())  # "s3cr3t"
print(item.item_id)  # "9876543210"

# Create or update
client.upsert("Homelab/My Secret", username="svc", password="newpass")

# Convenience field accessors (plaintext str, not SecretStr)
password = client.get_password("Homelab/My Secret")
username = client.get_username("Homelab/My Secret")
item_id = client.get_id("Homelab/My Secret")

# These read the local cache by default, so they can be stale after a
# rotation performed elsewhere.  Force a server read when it must be fresh:
password = client.get_password("Homelab/My Secret", sync=True)

# Check existence without raising
if client.item_exists("Homelab/My Secret"):
    print("found")
```

## API reference

### `LpassClient`

```python
LpassClient(username: str, auto_login: bool = True, timeout: float | None = 60.0)
```

The main entry point.  All methods raise subclasses of `LpassError` on failure.

Every sub-command except the interactive `login()` runs under `timeout` seconds
and raises `LpassTimeoutError` if it overruns — sync-forcing commands block on
the network, and an unbounded wait hangs the caller (for the bundled Ansible
vault-password script, that means the whole playbook, with no diagnostic).
Pass `timeout=None` to wait indefinitely.

| Method | Description |
|---|---|
| `is_logged_in() -> bool` | Check whether `lpass` has an active session. |
| `login()` | Run `lpass login` interactively. |
| `ensure_login()` | Login if needed; raises `LpassNotLoggedInError` in non-TTY sessions. |
| `item_exists(name) -> bool` | Return True if the item exists. |
| `get_item(name) -> LpassItem` | Fetch a full item; raises `LpassItemNotFoundError` if missing. |
| `get_field(name, flag, *, sync=False) -> str` | Fetch any single field by its `lpass show` flag, e.g. `--url`. Cached unless `sync=True`. |
| `get_password(name, *, sync=False) -> str` | Fetch only the Password field. Cached unless `sync=True`. |
| `get_username(name, *, sync=False) -> str` | Fetch only the Username field. Cached unless `sync=True`. |
| `get_id(name) -> str` | Fetch the numeric item ID, or `''` if not found. |
| `create(name, username, password) -> LpassItem` | Create a new login item. |
| `update(name, username, password) -> LpassItem` | Update an existing item. |
| `upsert(name, username, password) -> LpassItem` | Create or update. |

### Sync diagnostics

`lpass` uploads writes through a local queue in the background, so a command
can return successfully while the change is still only on disk.  A script that
writes a secret and exits immediately may never push it — and on the next run
a cache-based existence check won't see it, so the script creates the item a
second time.  LastPass does not enforce name uniqueness, so this is how
duplicate entries appear.  These three methods are the reason this wrapper
exists:

| Method | Description |
|---|---|
| `pending_sync_count() -> int` | Items written locally but not yet pushed (the `upload-queue/` directory). Non-zero is the usual cause of duplicate-item symptoms. |
| `failed_sync_count() -> int` | Items that permanently failed after five retries (`upload-fail/`, kept 14 days). Non-zero means writes were lost and need manual intervention. |
| `assert_sync_clean()` | Raise `LpassSyncError` if anything is pending or failed. Call it at the end of every script that writes. |

```python
client.upsert("Homelab/My Secret", username="svc", password=new_value)
client.assert_sync_clean()  # raises LpassSyncError if the upload didn't land
```

`LpassSyncError` subclasses `LpassError`, **not** `RuntimeError` — an
`except RuntimeError` will not catch it.

### `LpassItem`

Immutable Pydantic model representing a LastPass login item.

```python
item.name  # str — item path/title
item.item_id  # str — numeric LastPass ID
item.username  # str
item.password  # SecretStr — .get_secret_value() for the plaintext
item.url  # str
item.notes  # SecretStr — .get_secret_value() for the plaintext
```

`password` and `notes` are [`SecretStr`](https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr),
so they render as `**********` in `repr()`, `str()`, `model_dump()`,
`model_dump_json()`, and any structlog call that binds the item.  Reading the
plaintext takes an explicit `.get_secret_value()`, which keeps every such point
greppable.

The model is frozen, so updates return a copy:

| Method | Description |
|---|---|
| `with_password(password) -> LpassItem` | Copy with the password replaced. Accepts `str` or `SecretStr`; a plain `str` is wrapped for you. |
| `with_username(username) -> LpassItem` | Copy with the username replaced. |

### Exceptions

All exceptions inherit from `LpassError`.

| Exception | When raised |
|---|---|
| `LpassNotInstalledError` | The `lpass` binary is not on `PATH`. |
| `LpassNotLoggedInError` | Not authenticated and no TTY available for interactive login. |
| `LpassCommandError` | An `lpass` sub-command exited non-zero. Has `.command`, `.returncode`, `.stderr`. |
| `LpassItemNotFoundError` | Requested item does not exist. Has `.item_name`. |
| `LpassMultipleMatchesError` | A name matched more than one item. Has `.item_name`, `.count`. |
| `LpassParseError` | `lpass show` output could not be parsed. Has `.raw`. |
| `LpassSyncError` | `assert_sync_clean()` found pending or failed uploads. Has `.pending`, `.failed`. |
| `LpassTimeoutError` | An `lpass` sub-command exceeded the client timeout. Has `.command`, `.timeout`. |

## Ansible vault password script

The package installs a console script, `get-ansible-vault-pass-client`, that
prints an Ansible Vault password to stdout so Ansible can decrypt vaults
without a password file on disk.

Wire it into `ansible.cfg` via
[`vault_identity_list`](https://docs.ansible.com/ansible/latest/reference_appendices/config.html#default-vault-identity-list):

```ini
[defaults]
vault_identity_list = mylabel@.venv/bin/get-ansible-vault-pass-client
```

Ansible then calls it as `--vault-id mylabel`, and it looks that label up in a
`vault_pass_config.yml`:

```yaml
lastpass_username: you@example.com
vaults:
  mylabel: "1111111111111111111"    # LastPass item ID
  other:   "2222222222222222222"
```

The config file is located by, in order:

1. `$VAULT_PASS_CONFIG` — an absolute path to the file.
2. `vault_pass_config.yml` in the current working directory (i.e. wherever you
   run `ansible-playbook`).

Item IDs may be quoted or bare — unquoted YAML integers are coerced to strings.

| Option | Description |
|---|---|
| `--vault-id LABEL` | Vault identity label, passed automatically by Ansible. |
| `-v`, `--verbose` | Increase log verbosity (`-v` INFO, `-vv` DEBUG). Logs go to stderr. |

Every failure — missing or malformed config, unknown label, LastPass error —
prints a diagnostic to **stderr** and exits **1**, leaving stdout clean so
Ansible never mistakes an error message for a password.

```console
$ get-ansible-vault-pass-client --vault-id nosuch
Unknown vault ID 'nosuch'. Known labels: mylabel, other
```

> Because this script prints a password to stdout, anything else written to
> stdout would corrupt it.  That is why the library logs to stderr and why
> `setup_logging()` matters — see [Logging](#logging).

## Logging

lpass-wrap uses [structlog](https://www.structlog.org/).  CLI tools should call
`setup_logging()` early in `main()`:

```python
from lpass_wrap import setup_logging

setup_logging(verbose=args.verbose)  # 0=WARNING, 1=INFO, 2+=DEBUG
```

All log output goes to stderr — human-readable (`ConsoleRenderer`) when stderr
is a TTY, JSON lines when piped or redirected.

**Warning:** if the consuming application never configures structlog, its
default renderer prints to **stdout**, which will pollute any data your script
writes there.  Call `setup_logging()` (or configure structlog yourself) in
every CLI that prints data to stdout.

## AI-agent quickstart

For coding agents working *with* this library. If you are working *on* it, read
[AGENTS.md](AGENTS.md) instead.

```bash
pip install git+https://github.com/tod/lpass-wrap   # or: uv sync, in a clone
```

```python
from lpass_wrap import LpassClient, LpassItemNotFoundError, setup_logging

setup_logging()  # keeps library logs off stdout
client = LpassClient(username="you@example.com")
client.ensure_login()

try:
    password = client.get_password("Homelab/My Secret")
except LpassItemNotFoundError:
    ...
```

**Safety rules for generated code — these are the ones that bite:**

1. **Never pass a secret as a command-line argument.** Use this library rather
   than `subprocess` calls to `lpass`; argv is world-readable in process
   listings. This is the library's entire reason for existing.
2. **Call `assert_sync_clean()` after every write.** A write can return
   successfully and still be sitting in the local upload queue. Skipping this
   is how duplicate vault entries get created.
3. **`get_password()`/`get_username()` read a local cache by default** and can
   return a *stale* value after a rotation. Pass `sync=True` when the value
   must be authoritative — always when verifying a rotation.
4. **`item.password` is a `SecretStr`.** It renders as `**********`; call
   `.get_secret_value()` to read it. Do not add that call to reach a value you
   only intend to log.
5. **Prefer `upsert()`** over a conditional `create()`/`update()`; it uses an
   authoritative sync-forcing read for the existence check.

Known quirks and their reasoning live in
[`.claude/skills/lpass-wrap-usage/SKILL.md`](.claude/skills/lpass-wrap-usage/SKILL.md)
(consuming the library) and
[`.claude/skills/lpass-wrap-dev/SKILL.md`](.claude/skills/lpass-wrap-dev/SKILL.md)
(the lpass CLI's sync architecture). Security boundaries are in
[SECURITY.md](SECURITY.md).

## Development

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Lint, format-check, and type-check
uv run ruff check lpass_wrap tests
uv run ruff format --check lpass_wrap tests
uv run mypy .
uv run pyright
```

All four gates plus the test suite run in CI on Python 3.10–3.13. Every
subprocess call in the suite is mocked, so the tests need neither the `lpass`
binary nor a LastPass account.

Optionally install the [pre-commit](https://pre-commit.com/) hooks, which run
the same lint/format/type gates plus a
[gitleaks](https://github.com/gitleaks/gitleaks) secret scan:

```bash
uv run pre-commit install
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## License

Copyright 2026 Tod Detre.
Released under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).
