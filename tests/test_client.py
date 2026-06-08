# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for LpassClient.

All tests mock subprocess.run so lpass does not need to be installed.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from lpass_wrap import LpassClient, LpassItem
from lpass_wrap.exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotLoggedInError,
)

ITEM_NAME = "Homelab/Test Secret"

SHOW_JSON = json.dumps([{
    "id": "9876543210",
    "name": "Test Secret",
    "fullname": ITEM_NAME,
    "username": "svc",
    "password": "s3cr3t",
    "url": "",
    "note": "",
    "group": "Homelab",
    "last_modified_gmt": "",
    "last_touch": "",
}])

SHOW_JSON_MULTI = json.dumps([
    {
        "id": "111", "name": "Test Secret", "fullname": ITEM_NAME,
        "username": "svc", "password": "s3cr3t", "url": "", "note": "",
        "group": "Homelab", "last_modified_gmt": "", "last_touch": "",
    },
    {
        "id": "222", "name": "Test Secret", "fullname": ITEM_NAME,
        "username": "svc2", "password": "other", "url": "", "note": "",
        "group": "Homelab", "last_modified_gmt": "", "last_touch": "",
    },
])


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

    def test_parses_json_output(self) -> None:
        """get_item returns a fully populated LpassItem from lpass show --json output."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON)):
            item = LpassClient("u@example.com").get_item(ITEM_NAME)
        assert isinstance(item, LpassItem)
        assert item.name == ITEM_NAME
        assert item.item_id == "9876543210"
        assert item.username == "svc"
        assert item.password == "s3cr3t"

    def test_raises_when_not_found(self) -> None:
        """get_item raises LpassItemNotFoundError when lpass show exits non-zero."""
        with patch("subprocess.run", return_value=_make_proc(1)):
            with pytest.raises(LpassItemNotFoundError):
                LpassClient("u@example.com").get_item(ITEM_NAME)

    def test_raises_when_multiple_matches(self) -> None:
        """get_item raises LpassMultipleMatchesError when JSON contains more than one entry."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON_MULTI)):
            with pytest.raises(LpassMultipleMatchesError) as exc_info:
                LpassClient("u@example.com").get_item(ITEM_NAME)
        assert exc_info.value.count == 2
        assert exc_info.value.item_name == ITEM_NAME


class TestCreate:
    """Tests for LpassClient.create."""

    def test_calls_add_then_edit(self) -> None:
        """create calls lpass add --password then lpass edit --username."""
        client = LpassClient("u@example.com")
        calls: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append(cmd)
            return _make_proc(0, stdout=SHOW_JSON)

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
        """upsert delegates to create when get_item raises LpassItemNotFoundError."""
        client = LpassClient("u@example.com")
        with patch.object(client, "get_item", side_effect=LpassItemNotFoundError(ITEM_NAME)):
            with patch.object(client, "create", return_value=MagicMock()) as mock_create:
                client.upsert(ITEM_NAME, "svc", "s3cr3t")
                mock_create.assert_called_once_with(ITEM_NAME, "svc", "s3cr3t")

    def test_calls_update_when_item_exists(self) -> None:
        """upsert delegates to update when get_item succeeds (item found on server)."""
        client = LpassClient("u@example.com")
        with patch.object(client, "get_item", return_value=MagicMock()):
            with patch.object(client, "update", return_value=MagicMock()) as mock_update:
                client.upsert(ITEM_NAME, "svc", "newpass")
                mock_update.assert_called_once_with(ITEM_NAME, "svc", "newpass")


class TestPendingSyncCount:
    """Tests for LpassClient.pending_sync_count."""

    def test_returns_count_of_queue_files(self, tmp_path: "pytest.TempPathFactory") -> None:
        """pending_sync_count returns the number of files in the upload-queue."""
        queue = tmp_path / "upload-queue"
        queue.mkdir()
        (queue / "17808799730000").write_bytes(b"x" * 96)
        (queue / "17808799740000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 2

    def test_returns_zero_for_empty_queue(self, tmp_path: "pytest.TempPathFactory") -> None:
        """pending_sync_count returns 0 when the queue directory is empty."""
        (tmp_path / "upload-queue").mkdir()
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 0

    def test_returns_zero_when_queue_dir_missing(self, tmp_path: "pytest.TempPathFactory") -> None:
        """pending_sync_count returns 0 when upload-queue doesn't exist yet."""
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 0


class TestFailedSyncCount:
    """Tests for LpassClient.failed_sync_count."""

    def test_returns_count_of_failed_files(self, tmp_path: "pytest.TempPathFactory") -> None:
        """failed_sync_count returns the number of files in the upload-fail directory."""
        fail_dir = tmp_path / "upload-fail"
        fail_dir.mkdir()
        (fail_dir / "17808799730000").write_bytes(b"x" * 96)
        (fail_dir / "17808799740000").write_bytes(b"x" * 96)
        (fail_dir / "17808799750000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 3

    def test_returns_zero_for_empty_fail_dir(self, tmp_path: "pytest.TempPathFactory") -> None:
        """failed_sync_count returns 0 when the upload-fail directory is empty."""
        (tmp_path / "upload-fail").mkdir()
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 0

    def test_returns_zero_when_fail_dir_missing(self, tmp_path: "pytest.TempPathFactory") -> None:
        """failed_sync_count returns 0 when upload-fail doesn't exist."""
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 0
