# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for LpassClient.

All tests mock subprocess.run so lpass does not need to be installed.
"""

from unittest.mock import MagicMock, patch

import pytest

from lpass_wrap import LpassClient, LpassItem
from lpass_wrap.exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassNotLoggedInError,
)

ITEM_NAME = "Homelab/Test Secret"
SHOW_OUTPUT = f"{ITEM_NAME} [9876543210]\nUsername: svc\nPassword: s3cr3t\nURL: \nNotes: \n"


def _make_proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock CompletedProcess with the given attributes."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestIsLoggedIn:
    """Tests for LpassClient.is_logged_in."""

    def test_returns_true_when_lpass_exits_zero(self) -> None:
        """is_logged_in returns True when lpass status exits 0."""
        with patch("subprocess.run", return_value=_make_proc(0)):
            assert LpassClient("u@example.com").is_logged_in() is True

    def test_returns_false_when_lpass_exits_nonzero(self) -> None:
        """is_logged_in returns False when lpass status exits non-zero."""
        with patch("subprocess.run", return_value=_make_proc(1)):
            assert LpassClient("u@example.com").is_logged_in() is False


class TestEnsureLogin:
    """Tests for LpassClient.ensure_login."""

    def test_no_op_when_already_logged_in(self) -> None:
        """ensure_login does nothing when already authenticated."""
        client = LpassClient("u@example.com")
        with patch.object(client, "is_logged_in", return_value=True):
            with patch.object(client, "login") as mock_login:
                client.ensure_login()
                mock_login.assert_not_called()

    def test_raises_when_not_logged_in_and_not_tty(self) -> None:
        """ensure_login raises LpassNotLoggedInError in non-interactive sessions."""
        client = LpassClient("u@example.com")
        with patch.object(client, "is_logged_in", return_value=False):
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                with pytest.raises(LpassNotLoggedInError):
                    client.ensure_login()


class TestItemExists:
    """Tests for LpassClient.item_exists."""

    def test_returns_true_when_found(self) -> None:
        """item_exists returns True when lpass show exits 0."""
        with patch("subprocess.run", return_value=_make_proc(0)):
            assert LpassClient("u@example.com").item_exists(ITEM_NAME) is True

    def test_returns_false_when_not_found(self) -> None:
        """item_exists returns False when lpass show exits non-zero."""
        with patch("subprocess.run", return_value=_make_proc(1)):
            assert LpassClient("u@example.com").item_exists(ITEM_NAME) is False


class TestGetItem:
    """Tests for LpassClient.get_item."""

    def test_parses_show_output(self) -> None:
        """get_item returns a fully populated LpassItem from lpass show output."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_OUTPUT)):
            item = LpassClient("u@example.com").get_item(ITEM_NAME)
        assert isinstance(item, LpassItem)
        assert item.name == ITEM_NAME
        assert item.item_id == "9876543210"
        assert item.username == "svc"
        assert item.password == "s3cr3t"

    def test_raises_when_not_found(self) -> None:
        """get_item raises LpassItemNotFoundError when lpass show fails."""
        with patch("subprocess.run", return_value=_make_proc(1)):
            with pytest.raises(LpassItemNotFoundError):
                LpassClient("u@example.com").get_item(ITEM_NAME)


class TestCreate:
    """Tests for LpassClient.create."""

    def test_calls_add_then_edit(self) -> None:
        """create calls lpass add --password then lpass edit --username."""
        client = LpassClient("u@example.com")
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            return _make_proc(0, stdout=SHOW_OUTPUT)

        with patch("subprocess.run", side_effect=mock_run):
            client.create(ITEM_NAME, username="svc", password="s3cr3t")

        assert any("add" in c and "--password" in c for c in calls)
        assert any("edit" in c and "--username" in c for c in calls)

    def test_raises_on_lpass_failure(self) -> None:
        """create raises LpassCommandError when lpass add fails."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr="error")):
            with pytest.raises(LpassCommandError):
                LpassClient("u@example.com").create(ITEM_NAME, "svc", "s3cr3t")


class TestUpsert:
    """Tests for LpassClient.upsert."""

    def test_calls_create_when_item_does_not_exist(self) -> None:
        """upsert delegates to create when the item is not found."""
        client = LpassClient("u@example.com")
        with patch.object(client, "item_exists", return_value=False):
            with patch.object(client, "create", return_value=MagicMock()) as mock_create:
                client.upsert(ITEM_NAME, "svc", "s3cr3t")
                mock_create.assert_called_once_with(ITEM_NAME, "svc", "s3cr3t")

    def test_calls_update_when_item_exists(self) -> None:
        """upsert delegates to update when the item already exists."""
        client = LpassClient("u@example.com")
        with patch.object(client, "item_exists", return_value=True):
            with patch.object(client, "update", return_value=MagicMock()) as mock_update:
                client.upsert(ITEM_NAME, "svc", "newpass")
                mock_update.assert_called_once_with(ITEM_NAME, "svc", "newpass")
