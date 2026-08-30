# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the major version is `0`, breaking changes may land in a minor release.

## [0.2.0] — 2026-08-29

First public release. Relicensed, and hardened for use by people who are not
its author.

### Changed

- **BREAKING — `LpassItem.password` and `LpassItem.notes` are now
  [`SecretStr`](https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr)
  instead of `str`.** They render as `**********` in `repr()`, `str()`,
  `model_dump()`, `model_dump_json()`, and any structlog call that binds the
  item. Reading the plaintext now requires an explicit `.get_secret_value()`.

  *Why:* a library whose purpose is keeping secrets out of process listings
  should not leak them into logs and tracebacks instead.

  *Migration:* replace `item.password` with `item.password.get_secret_value()`
  wherever you need the plaintext. `notes` is included because it routinely
  holds recovery codes and API keys.

  `get_password()` and `get_username()` deliberately still return plain `str` —
  they exist to hand you the value, so keeping it out of logs is the caller's
  job. The bundled Ansible vault-password script depends on this: a `SecretStr`
  there would print `**********` and silently break every vault decrypt.

- **BREAKING — every `lpass` sub-command is now bounded by a timeout**, default
  60 seconds, configurable via `LpassClient(timeout=...)`. Overruns raise the
  new `LpassTimeoutError`. Pass `timeout=None` for the previous unbounded
  behaviour.

  *Why:* sync-forcing commands block on the network. As an Ansible
  vault-password script, a stalled connection previously hung the entire
  playbook indefinitely with no diagnostic.

  `login()` is exempt — it waits on a human typing a master password.

- **Relicensed from GPL-3.0-or-later to
  [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0).** Done while the
  project had a single author and no external contributors. `LICENSE` and
  `NOTICE` ship in the wheel.

- `client.py` now uses `pathlib` throughout instead of `os.path`/`os.listdir`,
  matching the rest of the package.

- `_run_lpass()` takes explicit keyword parameters instead of `**kwargs: Any`,
  closing a hole in strict type checking at the one boundary that handles
  secrets.

### Added

- **`sync=` keyword on `get_field()`, `get_password()`, and `get_username()`.**
  These read the local `lpass` cache (`--sync=no`) by default and can return a
  *stale* value after a rotation performed on another machine or in the web
  vault. Pass `sync=True` to force a server read — in particular when verifying
  that a rotation took effect. The staleness caveat is now documented on all
  three methods.
- `LpassTimeoutError`, carrying `.command` and `.timeout`. Exported from the
  package root.
- Tests asserting that a password does not appear in `repr(item)` or
  `model_dump_json()`. Suite grew from 57 to 76 tests.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `AGENTS.md`, and `llms.txt`.
- `.pre-commit-config.yaml` running ruff, ruff-format, mypy, and a gitleaks
  secret scan.
- GitHub Actions CI across Python 3.10, 3.11, 3.12, and 3.13.

### Fixed

- `get_id()`'s docstring claimed it parsed the `"Item Name [12345]"` first line
  of `lpass show` output. It has delegated to `get_item()` — which reads
  `--json` — since well before this release.
- `README.md` documented `pip install lpass-wrap`, but the package has never
  been published to PyPI. It now documents installing from the repository.
- `README.md` documented `pip install -e ".[dev]"` for development. Dev
  dependencies are declared in `[dependency-groups]`
  ([PEP 735](https://peps.python.org/pep-0735/)), not as an extra, so that
  command warned, installed no dev tools, and still exited 0. The correct
  command is `uv sync`.
- `README.md`'s API reference omitted `get_field()`, `pending_sync_count()`,
  `failed_sync_count()`, `assert_sync_clean()`, and
  `LpassItem.with_password()`/`with_username()` — including the sync-diagnostic
  methods that are the reason the library exists.
- The bundled `get-ansible-vault-pass-client` console script, its
  `vault_pass_config.yml` format, and its config-discovery order were entirely
  undocumented in the README.
- Bundled skill documentation stated that `assert_sync_clean()` raises
  `RuntimeError`; it raises `LpassSyncError`, which does **not** subclass
  `RuntimeError`. Following the old advice let the exception escape uncaught.
- Bundled skill documentation described a two-command create/update pattern
  (`lpass add` then `lpass edit`). It has been a single
  `lpass edit --non-interactive` call since well before this release.
- Removed machine-specific paths and a hardcoded email address from the bundled
  skill documentation, which assumed a particular author's checkout layout.

## [0.1.0]

Initial release. Private; never published.

- `LpassClient` wrapping the [LastPass CLI](https://github.com/lastpass/lastpass-cli),
  passing every secret via stdin so none reaches a process argument list.
- `LpassItem` pydantic model; typed exception hierarchy under `LpassError`.
- Sync diagnostics: `pending_sync_count()`, `failed_sync_count()`,
  `assert_sync_clean()`.
- `get-ansible-vault-pass-client` console script for Ansible
  `vault_identity_list`.
- structlog-based logging to stderr; `py.typed`; mypy `strict`.

[0.2.0]: https://github.com/tod/lpass-wrap/releases/tag/v0.2.0
[0.1.0]: https://github.com/tod/lpass-wrap/releases/tag/v0.1.0
