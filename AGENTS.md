# AGENTS.md

Operational facts for coding agents working **on** this repository. If you are
writing code that *consumes* the library, read the "AI-agent quickstart"
section of [README.md](README.md) instead.

## What this is

`lpass-wrap` is a small, typed Python wrapper around the
[LastPass CLI](https://github.com/lastpass/lastpass-cli) (`lpass`). Its reason
for existing is that secrets must never appear in a process argument list, and
that `lpass`'s background upload queue makes naive write-then-check logic
create duplicate vault entries.

About 1,200 lines of source and 800 of tests. Python 3.10+.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

**Do not use `pip install -e ".[dev]"`.** Dev dependencies are
[PEP 735](https://peps.python.org/pep-0735/) `[dependency-groups]`, not extras.
That command emits a warning, installs no dev tools, and exits **0** — so it
looks like it worked and then `pytest` is missing.

You need neither the `lpass` binary nor a LastPass account. Every subprocess
call in the test suite is mocked. **Never run tests against a real vault**, and
never write a test that would.

## Commands

```bash
uv run pytest                                  # 76 tests, ~0.4s
uv run ruff check lpass_wrap tests             # lint
uv run ruff format --check lpass_wrap tests    # format check (drop --check to apply)
uv run mypy .                                  # strict mode
uv run pyright                                 # standard mode
uv build                                       # wheel + sdist into dist/
```

All five gates must pass. CI runs them on Python 3.10–3.13.

## Layout

| Path | What lives there |
|---|---|
| `lpass_wrap/client.py` | `LpassClient` — everything that shells out to `lpass`. The file you will usually be editing. |
| `lpass_wrap/models.py` | `LpassItem`, a frozen pydantic model. |
| `lpass_wrap/exceptions.py` | The `LpassError` hierarchy. |
| `lpass_wrap/get_ansible_vault_pass_cli.py` | The `get-ansible-vault-pass-client` console script (typer). |
| `lpass_wrap/_logging.py` | `setup_logging()` — structlog to stderr. |
| `tests/test_client.py` | Client tests, all subprocess calls mocked. |
| `tests/test_cli.py` | Console-script tests. |
| `.claude/skills/` | Bundled reference docs on library usage and on `lpass` CLI behaviour. |

**Edit `.claude/skills/*/SKILL.md`, don't delete or rename them.** They are the
source of truth for the library's usage and debugging notes, and they are
consumed by tooling that reads them by path.

## Rules that will trip you up

1. **Secrets go over stdin, never argv.** Any new path passing a password or
   username to `lpass` must go through `_run_with_stdin()`. Command-line
   arguments are world-readable via `ps` and `/proc/<pid>/cmdline`. This is not
   a style preference; it is the entire point of the library.

2. **`LpassItem.password` and `.notes` are `SecretStr`.** They render as
   `**********`. Reading plaintext takes `.get_secret_value()`. Do not add that
   call just to make a log line or an f-string work.

3. **`model_copy(update=...)` does not re-validate.** A `with_*()` helper must
   wrap a plain `str` in `SecretStr` itself, or the raw string sits in the
   field and only explodes later, at some unrelated `.get_secret_value()` call
   site. There is a regression test for this.

4. **`get_password()`/`get_username()` deliberately return plain `str`, not
   `SecretStr`.** This asymmetry is intentional and load-bearing: the bundled
   Ansible vault-password script must `typer.echo()` the password to stdout,
   and a `SecretStr` there would print `**********` and silently break every
   vault decrypt. Do not "fix" it.

5. **Every subprocess call is bounded by a timeout** threaded from
   `LpassClient(timeout=...)`, mapping `subprocess.TimeoutExpired` to
   `LpassTimeoutError`. `login()` is the one deliberate exemption — it waits on
   a human typing a master password, and it does not capture output so `lpass`
   can drive its own prompt.

6. **Never `print()` to stdout from library code.** The vault-password script
   writes a password to stdout; anything else there corrupts it. Log to stderr
   via structlog.

7. **`--sync=no` reads a local cache and can be stale.** `get_field`,
   `get_password`, `get_username`, and `item_exists` all read the cache;
   `get_item` and all writes force `--sync=now`. `upsert()` uses `get_item()`
   rather than `item_exists()` for its existence check, on purpose — reverting
   that reintroduces the duplicate-entry bug.

8. **Docstrings on everything**, with `Args:`/`Returns:`/`Raises:`. The
   codebase is consistent about this.

## Sanitization rule

This is a public repository. Do not add to any file:

- machine-specific paths or checkout layouts
- personal email addresses (the one in `pyproject.toml` `authors` is deliberate
  and stays; do not add it elsewhere)
- internal hostnames or IP addresses
- real vault item names or vault-identity labels

Use `you@example.com`, `Homelab/My Secret`, and `mylabel` in all examples.
`gitleaks` runs as a pre-commit hook and in the sanitization gate, but it scans
for *secrets* — it will not catch personal paths or hostnames, so this one is
on you.

## Repository

- Remote: `origin` → `git@github.com:tod/lpass-wrap.git`
- License: Apache-2.0 (`LICENSE` + `NOTICE`, both shipped in the wheel)
- Not published to PyPI. `uv build` produces the artifacts; there is no
  automated publish step.
- Version lives in `pyproject.toml`; record every change in `CHANGELOG.md`
  ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format).
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/).

## Keeping docs true

Treat every documented command, flag, and config snippet as a testable claim.
If you change a signature or a flag, update `README.md`, `CHANGELOG.md`, the
bundled skills, and this file in the same commit. The documentation defects
fixed in 0.2.0 were all of this kind — commands that had never been run and
docstrings describing an implementation that had since been replaced.
