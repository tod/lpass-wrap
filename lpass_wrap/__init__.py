# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""lpass-wrap — Python wrapper around the LastPass CLI.

Public API::

    from lpass_wrap import LpassClient, LpassItem
    from lpass_wrap.exceptions import LpassError, LpassItemNotFoundError

    client = LpassClient(username="user@example.com")
    client.ensure_login()
    item = client.get_item("Homelab/My Secret")
    client.upsert("Homelab/My Secret", username="svc", password="newpass")
"""

from .client import LpassClient
from .exceptions import (
    LpassCommandError,
    LpassError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotLoggedInError,
    LpassParseError,
)
from .models import LpassItem

__all__ = [
    "LpassClient",
    "LpassItem",
    "LpassError",
    "LpassCommandError",
    "LpassItemNotFoundError",
    "LpassMultipleMatchesError",
    "LpassNotLoggedInError",
    "LpassParseError",
]
