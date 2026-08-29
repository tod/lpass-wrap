# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""lpass-wrap — Python wrapper around the LastPass CLI.

Public API::

    from lpass_wrap import LpassClient, LpassItem, setup_logging
    from lpass_wrap.exceptions import LpassError, LpassItemNotFoundError

    setup_logging()  # route library logs to stderr
    client = LpassClient(username="user@example.com")
    client.ensure_login()
    item = client.get_item("Homelab/My Secret")
    client.upsert("Homelab/My Secret", username="svc", password="newpass")

Logging note:
    The library logs via structlog.  If the consuming application never
    configures structlog, its default renderer prints to **stdout**, which
    can pollute a script's data stream (e.g. an Ansible vault password
    script).  Call :func:`setup_logging` (or configure structlog yourself)
    early in any CLI that prints data to stdout.
"""

from ._logging import setup_logging
from .client import LpassClient
from .exceptions import (
    LpassCommandError,
    LpassError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotInstalledError,
    LpassNotLoggedInError,
    LpassParseError,
    LpassSyncError,
)
from .models import LpassItem

__all__ = [
    "LpassClient",
    "LpassItem",
    "LpassError",
    "LpassCommandError",
    "LpassItemNotFoundError",
    "LpassMultipleMatchesError",
    "LpassNotInstalledError",
    "LpassNotLoggedInError",
    "LpassParseError",
    "LpassSyncError",
    "setup_logging",
]
