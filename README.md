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

```bash
pip install lpass-wrap
```

Or install from source in editable mode for development:

```bash
git clone https://github.com/tod-detre/lpass-wrap
cd lpass-wrap
pip install -e ".[dev]"
```

## Quick start

```python
from lpass_wrap import LpassClient

client = LpassClient(username="you@example.com")
client.ensure_login()   # prompts for master password if not already logged in

# Read an item
item = client.get_item("Homelab/My Secret")
print(item.username)   # "admin"
print(item.password)   # "s3cr3t"
print(item.item_id)    # "9876543210"

# Create or update
client.upsert("Homelab/My Secret", username="svc", password="newpass")

# Convenience field accessors
password = client.get_password("Homelab/My Secret")
username = client.get_username("Homelab/My Secret")
item_id  = client.get_id("Homelab/My Secret")

# Check existence without raising
if client.item_exists("Homelab/My Secret"):
    print("found")
```

## API reference

### `LpassClient`

```python
LpassClient(username: str, auto_login: bool = True)
```

The main entry point.  All methods raise subclasses of `LpassError` on failure.

| Method | Description |
|---|---|
| `is_logged_in() -> bool` | Check whether `lpass` has an active session. |
| `login()` | Run `lpass login` interactively. |
| `ensure_login()` | Login if needed; raises `LpassNotLoggedInError` in non-TTY sessions. |
| `item_exists(name) -> bool` | Return True if the item exists. |
| `get_item(name) -> LpassItem` | Fetch a full item; raises `LpassItemNotFoundError` if missing. |
| `get_password(name) -> str` | Fetch only the Password field. |
| `get_username(name) -> str` | Fetch only the Username field. |
| `get_id(name) -> str` | Fetch the numeric item ID, or `''` if not found. |
| `create(name, username, password) -> LpassItem` | Create a new login item. |
| `update(name, username, password) -> LpassItem` | Update an existing item. |
| `upsert(name, username, password) -> LpassItem` | Create or update. |

### `LpassItem`

Immutable Pydantic model representing a LastPass login item.

```python
item.name      # str — item path/title
item.item_id   # str — numeric LastPass ID
item.username  # str
item.password  # str
item.url       # str
item.notes     # str
```

### Exceptions

All exceptions inherit from `LpassError`.

| Exception | When raised |
|---|---|
| `LpassNotLoggedInError` | Not authenticated and no TTY available for interactive login. |
| `LpassCommandError` | An `lpass` sub-command exited non-zero. Has `.command`, `.returncode`, `.stderr`. |
| `LpassItemNotFoundError` | Requested item does not exist. Has `.item_name`. |
| `LpassParseError` | `lpass show` output could not be parsed. Has `.raw`. |

## Logging

lpass-wrap uses [structlog](https://www.structlog.org/).  CLI tools should call
`setup_logging()` early in `main()`:

```python
from lpass_wrap._logging import setup_logging
setup_logging(verbose=args.verbose)  # 0=WARNING, 1=INFO, 2+=DEBUG
```

Output is human-readable (`ConsoleRenderer`) when writing to a TTY, and JSON
lines when piped or redirected.

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint and type-check
ruff check lpass_wrap tests
pyright
```

## License

Copyright 2026 Tod Detre.
Released under the [GNU General Public License v3.0 or later](https://www.gnu.org/licenses/gpl-3.0.html).
