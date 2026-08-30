# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""Core LastPass client wrapping the lpass CLI.

All LastPass operations go through :class:`LpassClient`.  The client
communicates with the lpass binary via subprocess; passwords are always
passed via stdin rather than command-line arguments so they do not appear
in process listings.

Example::

    from lpass_wrap import LpassClient

    client = LpassClient(username="user@example.com")
    client.ensure_login()

    item = client.get_item("Homelab/My Secret")
    print(item.username, item.password.get_secret_value())

    client.upsert("Homelab/My Secret", username="svc", password="newpass")
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import structlog
from pydantic import SecretStr

from .exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotInstalledError,
    LpassNotLoggedInError,
    LpassParseError,
    LpassSyncError,
    LpassTimeoutError,
)
from .models import LpassItem

log = structlog.get_logger(__name__)

# lpass emits "Error: Could not find specified account(s)." for missing items
# (lastpass-cli show.c); matched case-insensitively to distinguish a genuine
# missing item from other failures (network, expired session, ...).
_NOT_FOUND_MARKER = "could not find"


def _subcommand_of(cmd: list[str]) -> str:
    """Return the lpass sub-command from a full command list, for error messages.

    Args:
        cmd: The full command list (including 'lpass' as the first element).

    Returns:
        The sub-command name (e.g. 'show'), or 'unknown' if the list is too short.
    """
    return cmd[1] if len(cmd) > 1 else "unknown"


def _run_lpass(
    cmd: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    input: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Run an lpass command, translating process-level failures into library errors.

    Parameters mirror the :func:`subprocess.run` arguments this library actually
    uses; they are spelled out rather than forwarded as ``**kwargs: Any``, which
    punched a hole in strict typing at the one boundary where it matters.

    Args:
        cmd:            The full command list (including 'lpass' as the first element).
        capture_output: Capture stdout and stderr instead of inheriting them.
        text:           Decode stdout/stderr (and encode ``input``) as text.
        input:          String to pipe to the process's stdin.
        timeout:        Wall-clock limit in seconds; None waits indefinitely.

    Returns:
        The completed process object.

    Raises:
        LpassNotInstalledError: If the lpass binary is not on PATH.
        LpassTimeoutError:      If the command exceeds ``timeout`` seconds.
    """
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=text,
            input=input,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise LpassNotInstalledError() from exc
    except subprocess.TimeoutExpired as exc:
        # timeout cannot be None here: subprocess only raises when one was set.
        raise LpassTimeoutError(_subcommand_of(cmd), timeout if timeout is not None else 0.0) from exc


def _lpass_data_dir() -> Path:
    """Resolve the directory where the lpass CLI keeps its data files.

    Replicates lastpass-cli's ``config_path_for_type()`` for CONFIG_DATA
    paths (which covers ``upload-queue/``, ``upload-fail/``, and ``blob``):

    1. ``$LPASS_HOME`` if set.
    2. ``$XDG_DATA_HOME/lpass`` if ``$XDG_DATA_HOME`` is set.
    3. ``~/.local/share/lpass`` if ``$XDG_RUNTIME_DIR`` is set (the CLI
       treats its presence as "this system uses XDG directories").
    4. ``~/.lpass`` otherwise (legacy fallback).

    Environment lookups stay on :data:`os.environ` (pathlib has no equivalent);
    every path the values are assembled into is a :class:`~pathlib.Path`.

    Returns:
        Absolute path of the lpass data directory.
    """
    lpass_home = os.environ.get("LPASS_HOME")
    if lpass_home:
        return Path(lpass_home)
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "lpass"
    if os.environ.get("XDG_RUNTIME_DIR"):
        return Path.home() / ".local" / "share" / "lpass"
    return Path.home() / ".lpass"


class LpassClient:
    """Client for interacting with the LastPass CLI (lpass).

    Wraps ``lpass`` sub-commands and provides a Python-native API for
    creating, reading, and updating LastPass login items.  All secret values
    are passed to lpass via stdin so they never appear in process argument
    lists or shell history.

    The client optionally auto-logs-in when not already authenticated,
    but only when running in an interactive TTY.  Set ``auto_login=False``
    to raise :class:`~lpass_wrap.exceptions.LpassNotLoggedInError` instead.

    Every lpass sub-command except the interactive :meth:`login` runs under
    ``timeout`` seconds (60 by default).  Sync-forcing commands block on the
    network, and an unbounded wait means a stalled connection hangs the caller
    — for the bundled Ansible vault-password script, that is the whole
    playbook, with no diagnostic.

    Example::

        client = LpassClient(username="user@example.com")
        client.ensure_login()
        client.upsert("Homelab/My Secret", username="admin", password="s3cr3t")
        item = client.get_item("Homelab/My Secret")
    """

    def __init__(self, username: str, auto_login: bool = True, timeout: float | None = 60.0) -> None:
        """Initialise the client.

        Args:
            username:   LastPass account email address used for ``lpass login``.
            auto_login: When True and not already authenticated, attempt an
                        interactive ``lpass login`` before the first operation.
                        Ignored in non-TTY sessions (raises instead).
            timeout:    Wall-clock limit in seconds applied to every lpass
                        sub-command except the interactive :meth:`login`.
                        Exceeding it raises
                        :class:`~lpass_wrap.exceptions.LpassTimeoutError`.
                        Pass None to wait indefinitely (the pre-0.2 behaviour).
        """
        self._username = username
        self._auto_login = auto_login
        self._timeout = timeout
        self._log = log.bind(username=username)

    # ── Authentication ─────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Return True if lpass currently has an active session.

        Returns:
            True if ``lpass status`` exits 0, False otherwise.

        Raises:
            LpassTimeoutError: If ``lpass status`` exceeds the client timeout.
        """
        result = _run_lpass(["lpass", "status"], capture_output=True, timeout=self._timeout)
        return result.returncode == 0

    def login(self) -> None:
        """Authenticate with LastPass interactively.

        Runs ``lpass login`` which prompts for the master password on the
        terminal.  Must be called from an interactive TTY.

        Note:
            This is the one command the client timeout does **not** apply to —
            it is waiting on a human typing a master password, and a 60-second
            cap would abort a legitimate login.  Output is likewise not
            captured, so that lpass can drive the terminal prompt; that is why
            the raised :class:`~lpass_wrap.exceptions.LpassCommandError` has an
            empty ``stderr`` and the real failure reason is lost.

        Raises:
            LpassCommandError: If ``lpass login`` exits with a non-zero status.
        """
        self._log.info("lpass_login_prompted")
        result = _run_lpass(["lpass", "login", self._username])
        if result.returncode != 0:
            raise LpassCommandError("login", result.returncode, "")

    def ensure_login(self) -> None:
        """Ensure lpass is authenticated, logging in interactively if needed.

        In a non-TTY session, raises immediately if not already logged in.

        Raises:
            LpassNotLoggedInError: If not logged in and not in an interactive TTY.
            LpassCommandError:     If the interactive login attempt fails.
        """
        if self.is_logged_in():
            return
        if not sys.stdin.isatty() or not self._auto_login:
            raise LpassNotLoggedInError("Not logged in to LastPass.  Run 'lpass login' and try again.")
        self.login()

    # ── Sync diagnostics ───────────────────────────────────────────────────

    def pending_sync_count(self) -> int:
        """Return the number of items waiting to be uploaded to LastPass.

        Reads the lpass upload-queue directory, which holds one file per item
        that has been written locally but not yet pushed to the server.  A
        non-zero count means previous writes may not be visible to other
        clients and is the likely cause of duplicate-item symptoms.

        The directory is resolved exactly as the lpass CLI resolves its data
        directory (``$LPASS_HOME``, then XDG paths, then ``~/.lpass``); see
        :func:`_lpass_data_dir`.

        Returns:
            Count of pending items (0 if the queue is empty or doesn't exist).
        """
        queue_dir = _lpass_data_dir() / "upload-queue"
        try:
            return len(list(queue_dir.iterdir()))
        except FileNotFoundError:
            return 0

    def failed_sync_count(self) -> int:
        """Return the number of items that permanently failed to upload.

        After five retry attempts with exponential backoff, the lpass CLI
        moves items from the upload-queue to ``upload-fail/``.  These entries
        remain there for up to 14 days before automatic cleanup.  A non-zero
        count means writes have been permanently lost from the LastPass servers
        and manual intervention is required.

        The directory is resolved exactly as the lpass CLI resolves its data
        directory (``$LPASS_HOME``, then XDG paths, then ``~/.lpass``); see
        :func:`_lpass_data_dir`.

        Returns:
            Count of permanently failed items (0 if the directory is empty or
            doesn't exist).
        """
        fail_dir = _lpass_data_dir() / "upload-fail"
        try:
            return len(list(fail_dir.iterdir()))
        except FileNotFoundError:
            return 0

    def assert_sync_clean(self) -> None:
        """Raise if any writes have not reached the LastPass server.

        Checks both the upload-queue (pending) and upload-fail (permanently
        failed) directories.  Call this at the end of any script that writes
        to LastPass to confirm the background sync process completed.

        Raises:
            LpassSyncError: If there are permanently failed items (checked
                first — more severe) or items still pending upload.
        """
        failed = self.failed_sync_count()
        pending = self.pending_sync_count()
        if failed:
            raise LpassSyncError(
                f"{failed} LastPass item(s) permanently failed to sync; "
                "check $LPASS_HOME/upload-fail/ and re-run manually.",
                pending=pending,
                failed=failed,
            )
        if pending:
            raise LpassSyncError(
                f"{pending} LastPass item(s) still pending upload — "
                "sync may be incomplete. Re-run or check network connectivity.",
                pending=pending,
                failed=failed,
            )

    # ── Item queries ───────────────────────────────────────────────────────

    def item_exists(self, item_name: str) -> bool:
        """Return True if a LastPass item with the given name exists.

        Uses ``--sync=no`` for speed: only the local blob is consulted, so the
        answer can be stale.  In particular, an item whose write is still in
        the upload-queue may report False here.  Use :meth:`get_item` (which
        forces ``--sync=now``) when the answer must be authoritative — see
        :meth:`upsert` for why.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            True if found, False otherwise.

        Raises:
            LpassTimeoutError: If lpass exceeds the client timeout.
        """
        result = _run_lpass(
            ["lpass", "show", "--sync=no", "--", item_name],
            capture_output=True,
            timeout=self._timeout,
        )
        return result.returncode == 0

    def get_item(self, item_name: str) -> LpassItem:
        """Fetch a LastPass login item by name.

        Forces a sync (``--sync=now``) so that items created in the same
        session are always visible.  Uses ``--json --expand-multi`` so that
        duplicate entries (multiple items sharing the same name) are detected
        reliably rather than silently returning garbage data.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            An :class:`~lpass_wrap.models.LpassItem` populated from lpass output.

        Raises:
            LpassItemNotFoundError:      If no item with that name exists.
            LpassMultipleMatchesError:   If more than one item shares that name.
            LpassParseError:             If the lpass JSON output cannot be parsed.
            LpassCommandError:           If lpass fails for any other reason
                                         (network, expired session, ...).
            LpassTimeoutError:           If lpass exceeds the client timeout.
        """
        result = _run_lpass(
            ["lpass", "show", "--sync=now", "--json", "--expand-multi", "--", item_name],
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if _NOT_FOUND_MARKER in stderr.lower():
                raise LpassItemNotFoundError(item_name)
            raise LpassCommandError("show", result.returncode, stderr)

        try:
            items = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LpassParseError(result.stdout) from exc

        if not items:
            raise LpassItemNotFoundError(item_name)
        if len(items) > 1:
            raise LpassMultipleMatchesError(item_name, len(items))

        return self._item_from_json(items[0], item_name)

    def get_field(self, item_name: str, flag: str, *, sync: bool = False) -> str:
        """Fetch a single field from a LastPass item.

        Warning:
            By default this reads the **local cache** (``--sync=no``) for speed,
            so the value can be *stale*.  After a password rotation performed
            elsewhere — another machine, the web vault, a concurrent script —
            this returns the old value until the cache catches up.  Pass
            ``sync=True`` whenever the answer must be authoritative, in
            particular when verifying a rotation.  :meth:`get_item` always
            syncs and does not need the flag.

        Args:
            item_name: The LastPass item name or folder path.
            flag:      The ``lpass show`` flag for the field, e.g. ``--password``.
            sync:      Force a server sync (``--sync=now``) before reading
                       instead of trusting the local cache.  Costs a network
                       round-trip.

        Returns:
            The field value with only the CLI's trailing newline removed —
            leading and trailing spaces that are part of the stored value are
            preserved.  This is **plaintext**: unlike :attr:`LpassItem.password`
            it is not wrapped in a ``SecretStr``, so the caller owns keeping it
            out of logs.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassCommandError:      If lpass exits non-zero for another reason.
            LpassTimeoutError:      If lpass exceeds the client timeout.
        """
        result: subprocess.CompletedProcess[str] = _run_lpass(
            ["lpass", "show", "--sync=now" if sync else "--sync=no", flag, "--", item_name],
            capture_output=True,
            text=True,
            timeout=self._timeout,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if _NOT_FOUND_MARKER in stderr.lower():
                raise LpassItemNotFoundError(item_name)
            raise LpassCommandError("show", result.returncode, stderr)
        # rstrip only the line terminator lpass appends; a password may legitimately
        # begin or end with spaces, and .strip() would silently corrupt it.
        return result.stdout.rstrip("\r\n")

    def get_password(self, item_name: str, *, sync: bool = False) -> str:
        """Return the Password field of a LastPass item.

        Warning:
            Reads the local cache by default and can therefore return a
            **stale** password after a rotation — see :meth:`get_field`.
            Pass ``sync=True`` when verifying a rotation.

        Args:
            item_name: The LastPass item name or folder path.
            sync:      Force a server sync (``--sync=now``) before reading.

        Returns:
            The password string, in plaintext.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassTimeoutError:      If lpass exceeds the client timeout.
        """
        return self.get_field(item_name, "--password", sync=sync)

    def get_username(self, item_name: str, *, sync: bool = False) -> str:
        """Return the Username field of a LastPass item.

        Warning:
            Reads the local cache by default and can therefore be **stale** —
            see :meth:`get_field`.

        Args:
            item_name: The LastPass item name or folder path.
            sync:      Force a server sync (``--sync=now``) before reading.

        Returns:
            The username string.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassTimeoutError:      If lpass exceeds the client timeout.
        """
        return self.get_field(item_name, "--username", sync=sync)

    def get_id(self, item_name: str) -> str:
        """Return the numeric LastPass item ID.

        Delegates to :meth:`get_item`, which reads the ``id`` key from
        ``lpass show --json`` — it does not parse the human-readable
        ``"Item Name [12345]"`` first line.  That also means it inherits
        ``--sync=now``, so the answer is authoritative.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            The numeric item ID string, or '' if not found or unparseable.

        Raises:
            LpassMultipleMatchesError: If more than one item shares that name.
            LpassCommandError:         If lpass fails for a reason other than
                                       a missing item.
            LpassTimeoutError:         If lpass exceeds the client timeout.
        """
        try:
            item = self.get_item(item_name)
            return item.item_id
        except (LpassItemNotFoundError, LpassParseError):
            return ""

    # ── Item mutations ─────────────────────────────────────────────────────

    def create(self, item_name: str, username: str, password: str | SecretStr) -> LpassItem:
        """Create a new LastPass login item.

        Uses a single ``lpass edit --non-interactive --sync=now`` call, which
        creates the item if it does not exist.  Both Username and Password are
        provided together via stdin as ``Field: value`` lines so neither value
        ever appears in a process argument list and only one upload occurs.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.  An empty string omits
                       the field entirely.
            password:  Value for the Password field.  Accepts a ``str`` or a
                       pydantic ``SecretStr`` (the latter is unwrapped).

        Returns:
            The newly created item fetched from LastPass.

        Raises:
            ValueError:        If username or password contains a newline.
            LpassCommandError: If the lpass command fails.
            LpassTimeoutError: If lpass exceeds the client timeout.
        """
        self._log.info("lpass_create", item=item_name)
        return self._edit_fields(item_name, username, password)

    def update(self, item_name: str, username: str, password: str | SecretStr) -> LpassItem:
        """Update the username and password of an existing LastPass login item.

        Uses a single ``lpass edit --non-interactive --sync=now`` call with
        both fields provided via stdin as ``Field: value`` lines.

        Note:
            Due to lpass CLI behaviour, if no item with ``item_name`` exists,
            ``lpass edit`` will silently create a new one rather than failing.
            Call :meth:`upsert` instead of this method directly when the item
            may or may not exist.

        Args:
            item_name: The LastPass item name or folder path.
            username:  New value for the Username field.  An empty string
                       leaves the existing Username unchanged (the field is
                       omitted from the edit) — it does NOT clear it.
            password:  New value for the Password field.  Accepts a ``str`` or a
                       pydantic ``SecretStr`` (the latter is unwrapped).

        Returns:
            The updated item fetched from LastPass.

        Raises:
            ValueError:        If username or password contains a newline.
            LpassCommandError: If the lpass command fails.
            LpassTimeoutError: If lpass exceeds the client timeout.
        """
        self._log.info("lpass_update", item=item_name)
        return self._edit_fields(item_name, username, password)

    def upsert(self, item_name: str, username: str, password: str | SecretStr) -> LpassItem:
        """Create or update a LastPass login item.

        Uses :meth:`get_item` (which forces ``--sync=now``) to check existence,
        avoiding the stale-cache race that :meth:`item_exists` (``--sync=no``)
        would introduce when items are still in the upload queue.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.
            password:  Value for the Password field.  Accepts a ``str`` or a
                       pydantic ``SecretStr`` (the latter is unwrapped).

        Returns:
            The created or updated item fetched from LastPass.

        Raises:
            LpassCommandError: If any lpass command fails.
            LpassTimeoutError: If lpass exceeds the client timeout.
        """
        try:
            self.get_item(item_name)
            return self.update(item_name, username, password)
        except LpassItemNotFoundError:
            return self.create(item_name, username, password)

    # ── Internals ──────────────────────────────────────────────────────────

    def _edit_fields(self, item_name: str, username: str, password: str | SecretStr) -> LpassItem:
        """Write Username/Password with one lpass edit call and re-fetch the item.

        Uses ``lpass edit --non-interactive --sync=now``, which creates the
        item if it does not exist.  Both fields are provided together via
        stdin as ``Field: value`` lines so neither value ever appears in a
        process argument list and only one upload occurs.  An empty username
        omits the Username line entirely.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field ('' to omit the field).
            password:  Value for the Password field.  A ``SecretStr`` is
                       unwrapped to its plaintext; a bare ``str`` is used as-is.
                       Passing a ``SecretStr`` without unwrapping would write
                       the literal string ``**********`` to LastPass.

        Returns:
            The item fetched from LastPass after the write.

        Raises:
            ValueError:        If username or password contains a newline —
                               the ``Field: value`` stdin format cannot
                               represent multi-line values, and interpolating
                               them would corrupt the record.
            LpassCommandError: If the lpass command fails.
            LpassTimeoutError: If lpass exceeds the client timeout.
        """
        if isinstance(password, SecretStr):
            password = password.get_secret_value()
        for label, value in (("username", username), ("password", password)):
            if "\n" in value or "\r" in value:
                raise ValueError(
                    f"{label} must not contain newline characters: "
                    "the lpass 'Field: value' input format cannot represent them"
                )
        fields = f"Username: {username}\nPassword: {password}\n" if username else f"Password: {password}\n"
        self._run_with_stdin(
            ["lpass", "edit", "--non-interactive", "--sync=now", "--", item_name],
            stdin=fields,
        )
        return self.get_item(item_name)

    def _run_with_stdin(self, cmd: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
        """Run an lpass command with a value piped to stdin.

        Args:
            cmd:   The full command list (including 'lpass' as the first element).
            stdin: The string to pipe to the process's stdin.

        Returns:
            The completed process object.

        Raises:
            LpassCommandError:      If the process exits with a non-zero status.
            LpassNotInstalledError: If the lpass binary is not on PATH.
            LpassTimeoutError:      If lpass exceeds the client timeout.
        """
        result = _run_lpass(cmd, input=stdin, text=True, capture_output=True, timeout=self._timeout)
        if result.returncode != 0:
            raise LpassCommandError(_subcommand_of(cmd), result.returncode, result.stderr)
        return result

    def _item_from_json(self, data: dict[str, str], item_name: str) -> LpassItem:
        """Build an :class:`LpassItem` from a single ``lpass show --json`` entry.

        Prefers the ``fullname`` reported by lpass over the query string, so
        the item's real path is returned even when the query was a numeric ID.

        Args:
            data:      One element from the JSON array returned by ``lpass show --json``.
            item_name: The query name (fallback if lpass omits ``fullname``).

        Returns:
            A populated :class:`~lpass_wrap.models.LpassItem`.
        """
        return LpassItem(
            name=data.get("fullname") or item_name,
            item_id=data.get("id", ""),
            username=data.get("username", ""),
            password=SecretStr(data.get("password", "")),
            url=data.get("url", ""),
            notes=SecretStr(data.get("note", "")),
        )
