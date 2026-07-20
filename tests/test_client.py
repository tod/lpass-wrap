# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for LpassClient.

All tests mock subprocess.run so lpass does not need to be installed.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lpass_wrap import LpassClient, LpassItem
from lpass_wrap.client import _lpass_data_dir
from lpass_wrap.exceptions import (
    LpassCommandError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotLoggedInError,
)

ITEM_NAME = "Homelab/Test Secret"

# Verbatim lpass CLI messages (lastpass-cli show.c / http errors).
NOT_FOUND_STDERR = "Error: Could not find specified account(s)."
NETWORK_STDERR = "Error: Peer certificate cannot be authenticated with given CA certificates."

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
        """get_item raises LpassItemNotFoundError on the CLI's 'could not find' message."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr=NOT_FOUND_STDERR)):
            with pytest.raises(LpassItemNotFoundError):
                LpassClient("u@example.com").get_item(ITEM_NAME)

    def test_raises_command_error_on_other_failure(self) -> None:
        """get_item raises LpassCommandError (not NotFound) when lpass fails for another reason."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr=NETWORK_STDERR)):
            with pytest.raises(LpassCommandError):
                LpassClient("u@example.com").get_item(ITEM_NAME)

    def test_raises_when_multiple_matches(self) -> None:
        """get_item raises LpassMultipleMatchesError when JSON contains more than one entry."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON_MULTI)):
            with pytest.raises(LpassMultipleMatchesError) as exc_info:
                LpassClient("u@example.com").get_item(ITEM_NAME)
        assert exc_info.value.count == 2
        assert exc_info.value.item_name == ITEM_NAME


class TestGetField:
    """Tests for LpassClient.get_field."""

    def test_returns_stripped_value(self) -> None:
        """get_field returns the field value with surrounding whitespace stripped."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="s3cr3t\n")):
            assert LpassClient("u@example.com").get_field(ITEM_NAME, "--password") == "s3cr3t"

    def test_raises_not_found_on_could_not_find_stderr(self) -> None:
        """get_field maps the CLI's 'could not find' message to LpassItemNotFoundError."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr=NOT_FOUND_STDERR)):
            with pytest.raises(LpassItemNotFoundError):
                LpassClient("u@example.com").get_field(ITEM_NAME, "--password")

    def test_raises_command_error_on_other_failure(self) -> None:
        """get_field raises LpassCommandError when lpass fails for another reason."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr=NETWORK_STDERR)):
            with pytest.raises(LpassCommandError):
                LpassClient("u@example.com").get_field(ITEM_NAME, "--password")


class TestLpassDataDir:
    """Tests for the _lpass_data_dir resolution chain (mirrors lastpass-cli config.c)."""

    def test_lpass_home_wins(self, tmp_path: Path) -> None:
        """$LPASS_HOME takes precedence over everything else."""
        env = {"LPASS_HOME": str(tmp_path), "XDG_DATA_HOME": "/xdg/data", "HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == str(tmp_path)

    def test_xdg_data_home_wins_without_runtime_dir(self) -> None:
        """$XDG_DATA_HOME is honoured even when $XDG_RUNTIME_DIR is unset."""
        env = {"XDG_DATA_HOME": "/xdg/data", "HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == "/xdg/data/lpass"

    def test_runtime_dir_implies_local_share_default(self) -> None:
        """$XDG_RUNTIME_DIR set without $XDG_DATA_HOME resolves to ~/.local/share/lpass."""
        env = {"XDG_RUNTIME_DIR": "/run/user/1000", "HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == "/home/u/.local/share/lpass"

    def test_legacy_fallback_without_any_xdg(self) -> None:
        """With no LPASS_HOME and no XDG variables, falls back to ~/.lpass."""
        env = {"HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == "/home/u/.lpass"


class TestCreate:
    """Tests for LpassClient.create."""

    def test_calls_single_edit_with_both_fields(self) -> None:
        """create uses one lpass edit --non-interactive --sync=now call with username and password in stdin."""
        client = LpassClient("u@example.com")
        calls: list[tuple[list[str], str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append((cmd, str(kwargs.get("input", ""))))
            return _make_proc(0, stdout=SHOW_JSON)

        with patch("subprocess.run", side_effect=mock_run):
            client.create(ITEM_NAME, username="svc", password="s3cr3t")

        edit_calls = [(cmd, inp) for cmd, inp in calls if "edit" in cmd]
        assert len(edit_calls) == 1
        cmd, stdin = edit_calls[0]
        assert "--non-interactive" in cmd
        assert "--sync=now" in cmd
        assert "Username: svc" in stdin
        assert "Password: s3cr3t" in stdin

    def test_raises_on_lpass_failure(self) -> None:
        """create raises LpassCommandError when lpass edit fails."""
        with patch("subprocess.run", return_value=_make_proc(1, stderr="error")):
            with pytest.raises(LpassCommandError):
                LpassClient("u@example.com").create(ITEM_NAME, "svc", "s3cr3t")

    @pytest.mark.parametrize("field", ["username", "password"])
    def test_rejects_newlines_in_values(self, field: str) -> None:
        """create raises ValueError before running lpass when a value contains a newline."""
        kwargs = {"username": "svc", "password": "s3cr3t", field: "evil\nNotes: injected"}
        with patch("subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="newline"):
                LpassClient("u@example.com").create(ITEM_NAME, **kwargs)
        mock_run.assert_not_called()


class TestUpdate:
    """Tests for LpassClient.update."""

    def test_calls_single_edit_with_both_fields(self) -> None:
        """update issues one lpass edit call with both fields in stdin, like create."""
        client = LpassClient("u@example.com")
        calls: list[tuple[list[str], str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            calls.append((cmd, str(kwargs.get("input", ""))))
            return _make_proc(0, stdout=SHOW_JSON)

        with patch("subprocess.run", side_effect=mock_run):
            client.update(ITEM_NAME, username="svc", password="newpass")

        edit_calls = [(cmd, inp) for cmd, inp in calls if "edit" in cmd]
        assert len(edit_calls) == 1
        cmd, stdin = edit_calls[0]
        assert "--non-interactive" in cmd
        assert "--sync=now" in cmd
        assert "Username: svc" in stdin
        assert "Password: newpass" in stdin

    def test_empty_username_omits_field(self) -> None:
        """update with an empty username sends only the Password line (leaves Username unchanged)."""
        client = LpassClient("u@example.com")
        stdins: list[str] = []

        def mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
            if "edit" in cmd:
                stdins.append(str(kwargs.get("input", "")))
            return _make_proc(0, stdout=SHOW_JSON)

        with patch("subprocess.run", side_effect=mock_run):
            client.update(ITEM_NAME, username="", password="newpass")

        assert stdins == ["Password: newpass\n"]


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

    def test_propagates_command_error_without_creating(self) -> None:
        """upsert must NOT create when the existence check fails for a non-'not found' reason.

        Regression guard for the duplicate-entry bug: a network/session failure
        during get_item used to be misread as 'item missing', triggering create
        and queueing a duplicate write.
        """
        client = LpassClient("u@example.com")
        err = LpassCommandError("show", 1, NETWORK_STDERR)
        with patch.object(client, "get_item", side_effect=err):
            with patch.object(client, "create") as mock_create:
                with pytest.raises(LpassCommandError):
                    client.upsert(ITEM_NAME, "svc", "s3cr3t")
        mock_create.assert_not_called()


class TestPendingSyncCount:
    """Tests for LpassClient.pending_sync_count."""

    def test_returns_count_of_queue_files(self, tmp_path: Path) -> None:
        """pending_sync_count returns the number of files in the upload-queue."""
        queue = tmp_path / "upload-queue"
        queue.mkdir()
        (queue / "17808799730000").write_bytes(b"x" * 96)
        (queue / "17808799740000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 2

    def test_returns_zero_for_empty_queue(self, tmp_path: Path) -> None:
        """pending_sync_count returns 0 when the queue directory is empty."""
        (tmp_path / "upload-queue").mkdir()
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 0

    def test_returns_zero_when_queue_dir_missing(self, tmp_path: Path) -> None:
        """pending_sync_count returns 0 when upload-queue doesn't exist yet."""
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").pending_sync_count() == 0


class TestFailedSyncCount:
    """Tests for LpassClient.failed_sync_count."""

    def test_returns_count_of_failed_files(self, tmp_path: Path) -> None:
        """failed_sync_count returns the number of files in the upload-fail directory."""
        fail_dir = tmp_path / "upload-fail"
        fail_dir.mkdir()
        (fail_dir / "17808799730000").write_bytes(b"x" * 96)
        (fail_dir / "17808799740000").write_bytes(b"x" * 96)
        (fail_dir / "17808799750000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 3

    def test_returns_zero_for_empty_fail_dir(self, tmp_path: Path) -> None:
        """failed_sync_count returns 0 when the upload-fail directory is empty."""
        (tmp_path / "upload-fail").mkdir()
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 0

    def test_returns_zero_when_fail_dir_missing(self, tmp_path: Path) -> None:
        """failed_sync_count returns 0 when upload-fail doesn't exist."""
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            assert LpassClient("u@example.com").failed_sync_count() == 0
