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

import re
import subprocess
import sys
from typing import TYPE_CHECKING

import structlog

from .exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassNotLoggedInError,
    LpassParseError,
)
from .models import LpassItem

if TYPE_CHECKING:
    pass

log = structlog.get_logger(__name__)

# Matches the first line of ``lpass show`` output: "Item Name [12345678901]"
_ID_RE = re.compile(r"\[(\d+)\]")


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
        session are always visible.

        Args:
            item_name: The LastPass item name or folder path.

        Returns:
            An :class:`~lpass_wrap.models.LpassItem` populated from lpass output.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassParseError:        If the lpass output cannot be parsed.
        """
        result = subprocess.run(
            ["lpass", "show", "--sync=now", item_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise LpassItemNotFoundError(item_name)

        return self._parse_show_output(item_name, result.stdout)

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

        Uses two lpass commands — ``add --password`` to create the item,
        then ``edit --username`` to set the username — because lpass field
        flags are boolean selectors that read a single value from stdin.
        This ensures neither value ever appears in a process argument list.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.
            password:  Value for the Password field.

        Returns:
            The newly created item fetched from LastPass.

        Raises:
            LpassCommandError: If either lpass command fails.
        """
        self._log.info("lpass_create", item=item_name)
        self._run_with_stdin(
            ["lpass", "add", "--non-interactive", "--sync=now", "--password", item_name],
            stdin=password,
        )
        if username:
            self._run_with_stdin(
                ["lpass", "edit", "--non-interactive", "--sync=now", "--username", item_name],
                stdin=username,
            )
        return self.get_item(item_name)

    def update(self, item_name: str, username: str, password: str) -> LpassItem:
        """Update the username and password of an existing LastPass login item.

        See :meth:`create` for the rationale behind the two-command approach.

        Args:
            item_name: The LastPass item name or folder path.
            username:  New value for the Username field.
            password:  New value for the Password field.

        Returns:
            The updated item fetched from LastPass.

        Raises:
            LpassItemNotFoundError: If no item with that name exists.
            LpassCommandError:      If either lpass command fails.
        """
        self._log.info("lpass_update", item=item_name)
        self._run_with_stdin(
            ["lpass", "edit", "--non-interactive", "--sync=now", "--password", item_name],
            stdin=password,
        )
        if username:
            self._run_with_stdin(
                ["lpass", "edit", "--non-interactive", "--sync=now", "--username", item_name],
                stdin=username,
            )
        return self.get_item(item_name)

    def upsert(self, item_name: str, username: str, password: str) -> LpassItem:
        """Create or update a LastPass login item.

        Checks whether the item already exists and calls :meth:`create` or
        :meth:`update` accordingly.

        Args:
            item_name: The LastPass item name or folder path.
            username:  Value for the Username field.
            password:  Value for the Password field.

        Returns:
            The created or updated item fetched from LastPass.

        Raises:
            LpassCommandError: If any lpass command fails.
        """
        if self.item_exists(item_name):
            return self.update(item_name, username, password)
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

    def _parse_show_output(self, item_name: str, output: str) -> LpassItem:
        """Parse the text output of ``lpass show`` into an :class:`LpassItem`.

        The first line has the format ``"Item Name [12345678901]"``.
        Subsequent lines are ``"Field: value"`` pairs.

        Args:
            item_name: The item name used for the query (used as fallback name).
            output:    Raw stdout from ``lpass show``.

        Returns:
            A populated :class:`~lpass_wrap.models.LpassItem`.

        Raises:
            LpassParseError: If the output is empty or cannot be parsed.
        """
        if not output.strip():
            raise LpassParseError(output)

        lines = output.splitlines()
        item_id = ""
        id_match = _ID_RE.search(lines[0])
        if id_match:
            item_id = id_match.group(1)

        fields: dict[str, str] = {}
        for line in lines[1:]:
            if ": " in line:
                key, _, value = line.partition(": ")
                fields[key.strip().lower()] = value.strip()

        return LpassItem(
            name=item_name,
            item_id=item_id,
            username=fields.get("username", ""),
            password=fields.get("password", ""),
            url=fields.get("url", ""),
            notes=fields.get("notes", ""),
        )
