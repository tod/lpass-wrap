# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exceptions raised by lpass-wrap."""


class LpassError(Exception):
    """Base class for all lpass-wrap exceptions."""


class LpassNotInstalledError(LpassError):
    """Raised when the lpass CLI binary cannot be found on PATH.

    Catch this to tell the user to install lastpass-cli (e.g.
    ``apt install lastpass-cli`` or ``brew install lastpass-cli``).
    """

    def __init__(self) -> None:
        """Initialise with a fixed install-hint message."""
        super().__init__(
            "lpass CLI not found on PATH. Install lastpass-cli "
            "(e.g. 'apt install lastpass-cli' or 'brew install lastpass-cli')."
        )


class LpassSyncError(LpassError):
    """Raised when local writes have not reached the LastPass server.

    Covers both items still pending in the upload-queue and items that
    permanently failed after retries (moved to ``upload-fail/``).  See
    :meth:`~lpass_wrap.client.LpassClient.assert_sync_clean`.

    Attributes:
        pending: Number of items still waiting in the upload-queue.
        failed:  Number of items that permanently failed to upload.
    """

    def __init__(self, message: str, pending: int, failed: int) -> None:
        """Initialise with the sync-state counts.

        Args:
            message: Human-readable description of the sync problem.
            pending: Number of items still waiting in the upload-queue.
            failed:  Number of items that permanently failed to upload.
        """
        self.pending = pending
        self.failed = failed
        super().__init__(message)


class LpassNotLoggedInError(LpassError):
    """Raised when lpass is not authenticated with LastPass.

    Catch this to prompt the user to run ``lpass login`` or call
    ``LpassClient.login()``.
    """


class LpassCommandError(LpassError):
    """Raised when an lpass sub-command exits with a non-zero status.

    Attributes:
        command:    The lpass sub-command that failed (e.g. 'add', 'show').
        returncode: The process exit code.
        stderr:     Captured stderr output from lpass.
    """

    def __init__(self, command: str, returncode: int, stderr: str) -> None:
        """Initialise with the failed command and its output.

        Args:
            command:    The lpass sub-command that failed.
            returncode: The process exit code.
            stderr:     Captured stderr output from lpass.
        """
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"lpass {command} failed (exit {returncode}): {stderr.strip()}")


class LpassItemNotFoundError(LpassError):
    """Raised when a requested LastPass item does not exist.

    Attributes:
        item_name: The item name or path that was not found.
    """

    def __init__(self, item_name: str) -> None:
        """Initialise with the missing item name.

        Args:
            item_name: The LastPass item name or path that was not found.
        """
        self.item_name = item_name
        super().__init__(f"LastPass item not found: {item_name!r}")


class LpassMultipleMatchesError(LpassError):
    """Raised when a name matches more than one LastPass item.

    Duplicate entries are typically caused by the upload-queue sync race:
    a write that failed to reach the server gets re-created on the next run.
    Use ``lpass ls`` to inspect duplicates and ``lpass rm`` with the unique
    item ID to remove them.

    Attributes:
        item_name: The item name or path that matched multiple entries.
        count:     The number of matching entries found.
    """

    def __init__(self, item_name: str, count: int) -> None:
        """Initialise with the item name and number of duplicates found.

        Args:
            item_name: The LastPass item name or path that matched multiple entries.
            count:     The number of matching entries found.
        """
        self.item_name = item_name
        self.count = count
        super().__init__(
            f"LastPass item {item_name!r} matched {count} entries; "
            "use lpass ls to inspect and lpass rm <UNIQUEID> to remove duplicates."
        )


class LpassParseError(LpassError):
    """Raised when lpass output cannot be parsed into an expected format.

    Attributes:
        raw: The raw lpass output that could not be parsed.
    """

    def __init__(self, raw: str) -> None:
        """Initialise with the unparseable output.

        Args:
            raw: The raw lpass output string.
        """
        self.raw = raw
        super().__init__(f"Could not parse lpass output: {raw!r}")
