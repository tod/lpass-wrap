# Contributing

Thanks for your interest. This is a small library with a narrow purpose:
a typed Python wrapper around the
[LastPass CLI](https://github.com/lastpass/lastpass-cli) that keeps secrets out
of process argument lists. Contributions that sharpen that purpose are very
welcome; contributions that broaden it are worth opening an issue about first.

## Development setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tod/lpass-wrap
cd lpass-wrap
uv sync
```

`uv sync` installs the dev tools from `[dependency-groups]`. **`pip install -e
".[dev]"` does not work** — dev dependencies are
[PEP 735](https://peps.python.org/pep-0735/) dependency groups, not extras, so
pip warns, installs nothing from the group, and still exits 0.

You do **not** need the `lpass` binary or a LastPass account to develop or test
this library. Every subprocess call in the test suite is mocked.

Optionally install the hooks:

```bash
uv run pre-commit install
```

## The gates

All five must pass before a change is merged. CI runs them on Python 3.10,
3.11, 3.12, and 3.13.

```bash
uv run ruff check lpass_wrap tests
uv run ruff format --check lpass_wrap tests
uv run mypy .
uv run pyright
uv run pytest
```

`ruff format` (without `--check`) applies the formatting. mypy runs in `strict`
mode and pyright in `standard` — new code is expected to be fully annotated,
and the package ships `py.typed`, so type errors here become type errors for
every downstream consumer.

## Conventions

**Docstrings on every class and method**, including private ones. Describe what
it does, with `Args:`, `Returns:`, and `Raises:` sections where they apply. The
existing code is consistent about this; please match it.

**Secrets go over stdin, never in argv.** Any new code path that passes a
password or username to `lpass` must use `_run_with_stdin()`. A command-line
argument is visible to every local user via `ps` and `/proc`. This is the
single rule the library exists to enforce — see [SECURITY.md](SECURITY.md).

**Every subprocess call takes a timeout.** `_run_lpass()` threads
`self._timeout` through and maps `subprocess.TimeoutExpired` to
`LpassTimeoutError`. The interactive `login()` is the only deliberate
exception, because it waits on a human.

**Secret-bearing model fields are `SecretStr`.** If you add a field that can
hold secret material, make it `SecretStr` and remember that
`model_copy(update=...)` does **not** re-validate — a `with_*()` helper must
wrap a plain `str` itself, or the unwrapped value sits in the field and fails
later at the `.get_secret_value()` call site.

**Raise a typed exception**, never a bare `RuntimeError` or `ValueError`.
Everything inherits from `LpassError` so consumers can catch the family. Give
new exceptions useful attributes (`.command`, `.item_name`, `.count`) rather
than encoding detail only in the message.

**Log to stderr via structlog.** Never `print()` to stdout from library code —
the bundled Ansible vault-password script writes a password to stdout, and
anything else there corrupts it.

## Tests

`pytest`, with every subprocess call mocked — see `tests/test_client.py` for
the patterns. New behaviour needs a test; bug fixes need a regression test.

If you touch anything secret-handling, add a test asserting the secret does
**not** appear where it shouldn't (`repr()`, `model_dump_json()`, log output).
There are existing examples to copy.

## Documentation

Docs are treated as testable claims, not prose. If you change a signature, a
flag, or a config format, update in the same commit:

- `README.md` — the API tables and any affected example
- `CHANGELOG.md` — under a new heading, following
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); mark breaking
  changes **BREAKING** and say how to migrate
- `AGENTS.md` and `llms.txt` — if the layout, commands, or entry points changed
- `.claude/skills/*/SKILL.md` — the bundled reference documentation

Do not add machine-specific paths, personal email addresses, internal
hostnames, or real vault item names to any of these. Use `you@example.com`,
`Homelab/My Secret`, and `mylabel`.

## Commits and pull requests

[Conventional Commits](https://www.conventionalcommits.org/) — `feat:`, `fix:`,
`docs:`, `refactor:`, `test:`, `chore:`, with a `!` for breaking changes.
Explain *why* in the body, not just what; the diff already says what.

Keep pull requests focused on one change, and confirm all five gates pass
locally first.

## Security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md) for private
reporting.
