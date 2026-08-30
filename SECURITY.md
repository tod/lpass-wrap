# Security Policy

## Reporting a vulnerability

Please report security issues **privately**, not as a public issue.

Use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository: **Security → Report a vulnerability**. If that is
unavailable, email the address in `pyproject.toml`'s `authors` field.

Please include the version, a description of the impact, and steps to
reproduce. This is a small project maintained by one person in their spare
time — expect an acknowledgement within about a week, and please allow
reasonable time for a fix before disclosing publicly.

Only the latest release is supported with security fixes.

## Threat model

Being explicit about what this library does and does not defend against.

### What it protects against

**Secrets in process argument lists.** This is the library's core purpose.
Every value that could be a secret is passed to `lpass` over **stdin**, never
as a command-line argument. Arguments are world-readable on Linux via
`/proc/<pid>/cmdline` and `ps`, so any secret placed there is visible to every
local user for the lifetime of the process, and often lands in shell history
too. `_run_with_stdin()` is the only path used for writes; passwords and
usernames never appear in argv.

**Secrets in logs, reprs, and tracebacks.** `LpassItem.password` and
`LpassItem.notes` are
[`SecretStr`](https://docs.pydantic.dev/latest/api/types/#pydantic.types.SecretStr).
They render as `**********` in `repr()`, `str()`, `model_dump()`,
`model_dump_json()`, and any structlog call that binds the item, so an
accidental `print(item)` or a logged exception context does not disclose them.

**Unbounded network waits.** Every `lpass` sub-command except the interactive
`login()` runs under a timeout (60 s by default). Without it, a stalled
connection hangs the caller indefinitely — for the bundled Ansible
vault-password script, that means an entire playbook, with no diagnostic.

### What it does *not* protect against

**Explicitly unwrapped secrets.** `.get_secret_value()`, `get_password()`, and
`get_username()` return plaintext `str`. This is deliberate — the getters exist
to hand you the value — but from that point on, keeping it out of logs, error
messages, and stdout is **the caller's responsibility**. Grep for
`get_secret_value` to find every place a secret leaves the model.

**An unlocked vault.** If the `lpass` agent holds an unlocked session, any
process running as that user can read every secret in the vault, with or
without this library. It provides no additional access control, and it
deliberately does not attempt to lock the vault on your behalf.

**A compromised local environment.** A malicious or trojaned `lpass` binary
earlier on `$PATH`, a compromised `$LPASS_HOME`, a debugger attached to the
process, or an attacker who can read the local encrypted `blob` and the agent's
key material all defeat this library entirely. It is a convenience and
correctness wrapper around a trusted local CLI, not a sandbox.

**Secrets in memory.** Plaintext secrets exist as ordinary Python `str` objects.
Python does not guarantee prompt zeroing, and they may persist in freed memory,
be written to swap, or appear in a core dump. `SecretStr` prevents accidental
*display*; it does not lock or scrub memory.

**`lpass` CLI debug logs.** Setting `LPASS_LOG_LEVEL` to 7 (DEBUG) or 8
(VERBOSE) makes the *lpass CLI itself* write session IDs and other secret
material in the clear to `$LPASS_HOME/lpass.log`. That file is outside this
library's control. Never share those logs unscrubbed.

**LastPass itself.** Any weakness in the LastPass service, its clients, or its
handling of your data is out of scope here.

### Known residual risks

**`stderr` in `LpassCommandError`.** When an `lpass` sub-command exits
non-zero, its raw stderr is captured into the exception and appears in
`str(exc)` and therefore in tracebacks. If `lpass` ever echoes an input value
back in an error message, that value would surface there. The risk is bounded —
`lpass` is not known to echo secret input on error, and secrets reach it via
stdin rather than argv — but it is not structurally prevented. Avoid logging
full tracebacks from untrusted contexts if this concerns you.

**`login()` reports an empty `stderr`.** `login()` does not capture output, so
that `lpass` can drive its own terminal password prompt. The trade-off is that
its `LpassCommandError` carries an empty `stderr` and the underlying reason for
a failed login is lost. This is a diagnosability cost, accepted deliberately;
it does not disclose anything.

**Stale reads.** `get_field()`, `get_password()`, and `get_username()` read the
local cache by default and can return a **superseded** password after a
rotation performed elsewhere. This is a correctness issue with security
consequences: a rotation-verification check that does not pass `sync=True` can
report success against a stale cached value. Pass `sync=True` whenever the
answer must be authoritative.

## Dependency and secret hygiene

The test suite mocks every subprocess call, so CI runs without the `lpass`
binary, without a LastPass account, and without any secrets configured. There
are no credentials in this repository's history — it is scanned with
[gitleaks](https://github.com/gitleaks/gitleaks), which also runs as a
pre-commit hook.
