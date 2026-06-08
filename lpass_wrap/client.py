# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

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
    print(item.username, item.password)

    client.upsert("Homelab/My Secret", username="svc", password="newpass")
"""

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import structlog

from .exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotLoggedInError,
    LpassParseError,
)
from .models import LpassItem

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)


class LpassClient:
    """Client for interacting with the LastPass CLI (lpass).

    Wraps ``lpass`` sub-commands and provides a Python-native API for
    creating, reading, and updating LastPass login items.  All secret values
    are passed to lpass via stdin so they never appear in process argument
    lists or shell history.

    The client optionally auto-logs-in when not already authenticated,
    but only when running in an interactive TTY.  Set ``auto_login=False``
    to raise :class:`~lpass_wrap.exceptions.LpassNotLoggedInError` instead.

    Example::

        client = LpassClient(username="user@example.com")
        client.ensure_login()
        client.upsert("Homelab/My Secret", username="admin", password="s3cr3t")
        item = client.get_item("Homelab/My Secret")
    """

    def __init__(self, username: str, auto_login: bool = True) -> None:
        """Initialise the client.

        Args:
            username:   LastPass account email address used for ``lpass login``.
            auto_login: When True and not already authenticated, attempt an
                        interactive ``lpass login`` before the first operation.
                        Ignored in non-TTY sessions (raises instead).
        """
        self._username = username
        self._auto_login = auto_login
        self._log = log.bind(username=username)

    # ── Authentication ─────────────────────────────────────────────────────

    def is_logged_in(self) -> bool:
        """Return True if lpass currently has an active session.

        Returns:
            True if ``lpass status`` exits 0, False otherwise.
        """
        result = subprocess.run(["lpass", "status"], capture_output=True)
        return result.returncode == 0

    def login(self) -> None:
        """Authenticate with LastPass interactively.

        Runs ``lpass login`` which prompts for the master password on the
        terminal.  Must be called from an interactive TTY.

        Raises:
            LpassCommandError: If ``lpass login`` exits with a non-zero status.
        """
        self._log.info("lpass_login_prompted")
        result = subprocess.run(["lpass", "login", self._username])
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
            raise LpassNotLoggedInError(
                "Not logged in to LastPass.  Run 'lpass login' and try again."
            )
        self.login()

    # ── Sync diagnostics ───────────────────────────────────────────────────

    def pending_sync_count(self) -> int:
        """Return the number of items waiting to be uploaded to LastPass.

        Reads the lpass upload-queue directory, which holds one file per item
        that has been written locally but not yet pushed to the server.  A
        non-zero count means previous writes may not be visible to other
        clients and is the likely cause of duplicate-item symptoms.

        The directory is resolved from ``$LPASS_HOME`` (defaulting to
        ``~/.lpass``), matching how the lpass CLI itself locates it.

        Returns:
            Count of pending items (0 if the queue is empty or doesn't exist).
        """
        lpass_home = os.environ.get("LPASS_HOME", os.path.expanduser("~/.lpass"))
        queue_dir = os.path.join(lpass_home, "upload-queue")
        try:
            return len(os.listdir(queue_dir))
        except FileNotFoundError:
            return 0

    def failed_sync_count(self) -> int:
        """Return the number of items that permanently failed to upload.

        After five retry attempts with exponential backoff, the lpass CLI
        moves items from the upload-queue to ``upload-fail/``.  These entries
        remain there for up to 14 days before automatic cleanup.  A non-zero
        count means writes have been permanently lost from the LastPass servers
        and manual intervention is required.

        The directory is resolved from ``$LPASS_HOME`` (defaulting to
        ``~/.lpass``), matching how the lpass CLI itself locates it.

        Returns:
            Count of permanently failed items (0 if the directory is empty or
            doesn't exist).
        """
        lpass_home = os.environ.get("LPASS_HOME", os.path.expanduser("~/.lpass"))
        fail_dir = os.path.join(lpass_home, "upload-fail")
        try:
            return len(os.listdir(fail_dir))
        except FileNotFoundError:
            return 0

    def assert_sync_clean(self) -> None:
        """Raise if any writes have not reached the LastPass server.

        Checks both the upload-queue (pending) and upload-fail (permanently
        failed) directories.  Call this at the end of any script that writes
        to LastPass to confirm the background sync process completed.

        Raises:
            RuntimeError: If there are permanently failed items (checked
                first — more severe) or items still pending upload.
        """
        failed = self.failed_sync_count()
        pending = self.pending_sync_count()
        if failed:
            raise RuntimeError(
                f"{failed} LastPass item(s) permanently failed to sync; "
                "check $LPASS_HOME/upload-fail/ and re-run manually."
            )
        if pending:
            raise RuntimeError(
                f"{pending} LastPass item(s) still pending upload — "
                "sync may be incomplete. Re-run or check network connectivity."
            )

    # ── Item queries ───────────────────────────────────────────────────────

    def item_exists(self, item_name: str) -> bool:
        """Return True if a LastPass item with the given name exists.

        Uses ``--sync=no`` for speed; the local cache is up-to-date after
        any write operation that uses ``--sync=now``.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            True if found, False otherwise.
        """
        result = subprocess.run(
            ["lpass", "show", "--sync=no", item_name],
            capture_output=True,
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
        """
        result = subprocess.run(
            ["lpass", "show", "--sync=now", "--json", "--expand-multi", item_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise LpassItemNotFoundError(item_name)

        try:
            items = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LpassParseError(result.stdout) from exc

        if not items:
            raise LpassItemNotFoundError(item_name)
        if len(items) > 1:
            raise LpassMultipleMatchesError(item_name, len(items))

        return self._item_from_json(items[0], item_name)

    def get_field(self, item_name: str, flag: str) -> str:
        """Fetch a single field from a LastPass item.

        Args:
            item_name: The LastPass item name or folder path.
            flag:      The ``lpass show`` flag for the field, e.g. ``--password``.

        Returns:
            The field value, stripped of whitespace.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassCommandError:      If lpass exits non-zero for another reason.
        """
        result = subprocess.run(
            ["lpass", "show", "--sync=no", flag, item_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "not found" in stderr.lower():
                raise LpassItemNotFoundError(item_name)
            raise LpassCommandError("show", result.returncode, stderr)
        return result.stdout.strip()

    def get_password(self, item_name: str) -> str:
        """Return the Password field of a LastPass item.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            The password string.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
        """
        return self.get_field(item_name, "--password")

    def get_username(self, item_name: str) -> str:
        """Return the Username field of a LastPass item.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            The username string.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
        """
        return self.get_field(item_name, "--username")

    def get_id(self, item_name: str) -> str:
        """Return the numeric LastPass item ID.

        Parses the ``lpass show`` first-line format ``"Item Name [12345]"``.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            The numeric item ID string, or '' if not found or unparseable.
        """
        try:
            item = self.get_item(item_name)
            return item.item_id
        except (LpassItemNotFoundError, LpassParseError):
            return ""

    # ── Item mutations ─────────────────────────────────────────────────────

    def create(self, item_name: str, username: str, password: str) -> LpassItem:
        """Create a new LastPass login item.

        Uses a single ``lpass edit --non-interactive --sync=now`` call, which
        creates the item if it does not exist.  Both Username and Password are
        provided together via stdin as ``Field: value`` lines so neither value
        ever appears in a process argument list and only one upload occurs.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.
            password:  Value for the Password field.

        Returns:
            The newly created item fetched from LastPass.

        Raises:
            LpassCommandError: If the lpass command fails.
        """
        self._log.info("lpass_create", item=item_name)
        fields = f"Username: {username}\nPassword: {password}\n" if username else f"Password: {password}\n"
        self._run_with_stdin(
            ["lpass", "edit", "--non-interactive", "--sync=now", item_name],
            stdin=fields,
        )
        return self.get_item(item_name)

    def update(self, item_name: str, username: str, password: str) -> LpassItem:
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
            username:  New value for the Username field.
            password:  New value for the Password field.

        Returns:
            The updated item fetched from LastPass.

        Raises:
            LpassCommandError: If the lpass command fails.
        """
        self._log.info("lpass_update", item=item_name)
        fields = f"Username: {username}\nPassword: {password}\n" if username else f"Password: {password}\n"
        self._run_with_stdin(
            ["lpass", "edit", "--non-interactive", "--sync=now", item_name],
            stdin=fields,
        )
        return self.get_item(item_name)

    def upsert(self, item_name: str, username: str, password: str) -> LpassItem:
        """Create or update a LastPass login item.

        Uses :meth:`get_item` (which forces ``--sync=now``) to check existence,
        avoiding the stale-cache race that :meth:`item_exists` (``--sync=no``)
        would introduce when items are still in the upload queue.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.
            password:  Value for the Password field.

        Returns:
            The created or updated item fetched from LastPass.

        Raises:
            LpassCommandError: If any lpass command fails.
        """
        try:
            self.get_item(item_name)
            return self.update(item_name, username, password)
        except LpassItemNotFoundError:
            return self.create(item_name, username, password)

    # ── Internals ──────────────────────────────────────────────────────────

    def _run_with_stdin(self, cmd: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
        """Run an lpass command with a value piped to stdin.

        Args:
            cmd:   The full command list (including 'lpass' as the first element).
            stdin: The string to pipe to the process's stdin.

        Returns:
            The completed process object.

        Raises:
            LpassCommandError: If the process exits with a non-zero status.
        """
        result = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
        if result.returncode != 0:
            subcommand = cmd[1] if len(cmd) > 1 else "unknown"
            raise LpassCommandError(subcommand, result.returncode, result.stderr)
        return result

    def _item_from_json(self, data: dict, item_name: str) -> LpassItem:
        """Build an :class:`LpassItem` from a single ``lpass show --json`` entry.

        Args:
            data:      One element from the JSON array returned by ``lpass show --json``.
            item_name: The query name (used as the ``name`` field fallback).

        Returns:
            A populated :class:`~lpass_wrap.models.LpassItem`.
        """
        return LpassItem(
            name=item_name,
            item_id=data.get("id", ""),
            username=data.get("username", ""),
            password=data.get("password", ""),
            url=data.get("url", ""),
            notes=data.get("note", ""),
        )
