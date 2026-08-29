# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for LpassClient.

All tests mock subprocess.run so lpass does not need to be installed.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from lpass_wrap import LpassClient, LpassItem
from lpass_wrap.client import _lpass_data_dir
from lpass_wrap.exceptions import (
    LpassCommandError,
    LpassError,
    LpassItemNotFoundError,
    LpassMultipleMatchesError,
    LpassNotInstalledError,
    LpassNotLoggedInError,
    LpassSyncError,
    LpassTimeoutError,
)

ITEM_NAME = "Homelab/Test Secret"

# Verbatim lpass CLI messages (lastpass-cli show.c / http errors).
NOT_FOUND_STDERR = "Error: Could not find specified account(s)."
NETWORK_STDERR = "Error: Peer certificate cannot be authenticated with given CA certificates."

SHOW_JSON = json.dumps(
    [
        {
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
        }
    ]
)

SHOW_JSON_MULTI = json.dumps(
    [
        {
            "id": "111",
            "name": "Test Secret",
            "fullname": ITEM_NAME,
            "username": "svc",
            "password": "s3cr3t",
            "url": "",
            "note": "",
            "group": "Homelab",
            "last_modified_gmt": "",
            "last_touch": "",
        },
        {
            "id": "222",
            "name": "Test Secret",
            "fullname": ITEM_NAME,
            "username": "svc2",
            "password": "other",
            "url": "",
            "note": "",
            "group": "Homelab",
            "last_modified_gmt": "",
            "last_touch": "",
        },
    ]
)


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
        assert item.password.get_secret_value() == "s3cr3t"

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
            assert _lpass_data_dir() == tmp_path

    def test_xdg_data_home_wins_without_runtime_dir(self) -> None:
        """$XDG_DATA_HOME is honoured even when $XDG_RUNTIME_DIR is unset."""
        env = {"XDG_DATA_HOME": "/xdg/data", "HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == Path("/xdg/data/lpass")

    def test_runtime_dir_implies_local_share_default(self) -> None:
        """$XDG_RUNTIME_DIR set without $XDG_DATA_HOME resolves to ~/.local/share/lpass."""
        env = {"XDG_RUNTIME_DIR": "/run/user/1000", "HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == Path("/home/u/.local/share/lpass")

    def test_legacy_fallback_without_any_xdg(self) -> None:
        """With no LPASS_HOME and no XDG variables, falls back to ~/.lpass."""
        env = {"HOME": "/home/u"}
        with patch.dict("os.environ", env, clear=True):
            assert _lpass_data_dir() == Path("/home/u/.lpass")


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


class TestAssertSyncClean:
    """Tests for LpassClient.assert_sync_clean."""

    def test_passes_when_both_dirs_empty(self, tmp_path: Path) -> None:
        """assert_sync_clean returns silently when nothing is queued or failed."""
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            LpassClient("u@example.com").assert_sync_clean()

    def test_raises_sync_error_for_pending_items(self, tmp_path: Path) -> None:
        """Pending upload-queue items raise LpassSyncError with the counts attached."""
        queue_dir = tmp_path / "upload-queue"
        queue_dir.mkdir()
        (queue_dir / "17808799730000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            with pytest.raises(LpassSyncError) as excinfo:
                LpassClient("u@example.com").assert_sync_clean()
        assert excinfo.value.pending == 1
        assert excinfo.value.failed == 0

    def test_raises_sync_error_for_failed_items_first(self, tmp_path: Path) -> None:
        """Failed items take precedence in the message even when items are also pending."""
        for sub in ("upload-queue", "upload-fail"):
            d = tmp_path / sub
            d.mkdir()
            (d / "17808799730000").write_bytes(b"x" * 96)
        with patch.dict("os.environ", {"LPASS_HOME": str(tmp_path)}):
            with pytest.raises(LpassSyncError, match="permanently failed") as excinfo:
                LpassClient("u@example.com").assert_sync_clean()
        assert excinfo.value.pending == 1
        assert excinfo.value.failed == 1

    def test_sync_error_is_an_lpass_error(self) -> None:
        """LpassSyncError participates in the LpassError hierarchy."""
        assert issubclass(LpassSyncError, LpassError)


class TestLpassNotInstalled:
    """Tests for the missing-binary translation in _run_lpass."""

    def test_missing_binary_raises_not_installed(self) -> None:
        """A FileNotFoundError from subprocess.run becomes LpassNotInstalledError."""
        with patch("subprocess.run", side_effect=FileNotFoundError("lpass")):
            with pytest.raises(LpassNotInstalledError, match="lastpass-cli"):
                LpassClient("u@example.com").is_logged_in()

    def test_missing_binary_on_write_path(self) -> None:
        """The stdin write path raises LpassNotInstalledError too."""
        with patch("subprocess.run", side_effect=FileNotFoundError("lpass")):
            with pytest.raises(LpassNotInstalledError):
                LpassClient("u@example.com").create(ITEM_NAME, "svc", "pw")


class TestItemFromJsonFullname:
    """Tests for the fullname preference in _item_from_json."""

    def test_fullname_wins_over_query_string(self) -> None:
        """When querying by numeric ID, the item's real path is returned as name."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON)):
            item = LpassClient("u@example.com").get_item("9876543210")
        assert item.name == ITEM_NAME

    def test_query_name_used_when_fullname_absent(self) -> None:
        """The query string is the fallback when lpass omits fullname."""
        payload = json.dumps([{"id": "1", "username": "u", "password": "p"}])
        with patch("subprocess.run", return_value=_make_proc(0, stdout=payload)):
            item = LpassClient("u@example.com").get_item(ITEM_NAME)
        assert item.name == ITEM_NAME


class TestSecretRedaction:
    """Tests that secret-bearing fields never render in plaintext (S1).

    The library exists to keep secrets out of process listings; leaking them
    into logs and tracebacks through a model repr would undo that.
    """

    def test_password_is_a_secretstr(self) -> None:
        """A plain string passed to the model is coerced to SecretStr by pydantic."""
        item = LpassItem(name=ITEM_NAME, password=SecretStr("s3cr3t"))
        assert isinstance(item.password, SecretStr)
        assert item.password.get_secret_value() == "s3cr3t"

    def test_password_absent_from_repr_and_str(self) -> None:
        """repr() and str() of an item must not contain the plaintext password."""
        item = LpassItem(name=ITEM_NAME, username="svc", password=SecretStr("s3cr3t"))
        assert "s3cr3t" not in repr(item)
        assert "s3cr3t" not in str(item)

    def test_password_absent_from_model_dumps(self) -> None:
        """model_dump() and model_dump_json() must not leak the plaintext password."""
        item = LpassItem(name=ITEM_NAME, username="svc", password=SecretStr("s3cr3t"))
        assert "s3cr3t" not in str(item.model_dump())
        assert "s3cr3t" not in item.model_dump_json()

    def test_notes_are_redacted_too(self) -> None:
        """Notes can hold recovery codes and API keys, so they are secret as well."""
        item = LpassItem(name=ITEM_NAME, notes=SecretStr("recovery-code-42"))
        assert "recovery-code-42" not in repr(item)
        assert "recovery-code-42" not in item.model_dump_json()
        assert item.notes.get_secret_value() == "recovery-code-42"

    def test_item_from_json_wraps_secrets(self) -> None:
        """An item built from real lpass --json output carries wrapped secrets."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON)):
            item = LpassClient("u@example.com").get_item(ITEM_NAME)
        assert isinstance(item.password, SecretStr)
        assert isinstance(item.notes, SecretStr)
        assert "s3cr3t" not in repr(item)

    def test_with_password_wraps_a_plain_string(self) -> None:
        """model_copy does not re-validate, so with_password must wrap str itself.

        Regression guard: an unwrapped str would sit in a SecretStr field and
        blow up at the .get_secret_value() call site instead of here.
        """
        item = LpassItem(name=ITEM_NAME, password=SecretStr("old")).with_password("new")
        assert isinstance(item.password, SecretStr)
        assert item.password.get_secret_value() == "new"
        assert "new" not in repr(item)

    def test_with_password_accepts_a_secretstr(self) -> None:
        """An already-wrapped SecretStr is passed through unchanged."""
        item = LpassItem(name=ITEM_NAME).with_password(SecretStr("new"))
        assert item.password.get_secret_value() == "new"


class TestTimeout:
    """Tests for the subprocess timeout plumbing (S2)."""

    def test_default_timeout_passed_to_subprocess(self) -> None:
        """Every non-interactive command runs under the 60s default timeout."""
        with patch("subprocess.run", return_value=_make_proc(0)) as mock_run:
            LpassClient("u@example.com").is_logged_in()
        assert mock_run.call_args.kwargs["timeout"] == 60.0

    def test_custom_timeout_is_honoured(self) -> None:
        """A client-level timeout overrides the default on every call."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout=SHOW_JSON)) as mock_run:
            LpassClient("u@example.com", timeout=5.0).get_item(ITEM_NAME)
        assert mock_run.call_args.kwargs["timeout"] == 5.0

    def test_timeout_none_waits_indefinitely(self) -> None:
        """timeout=None restores the unbounded pre-0.2 behaviour."""
        with patch("subprocess.run", return_value=_make_proc(0)) as mock_run:
            LpassClient("u@example.com", timeout=None).is_logged_in()
        assert mock_run.call_args.kwargs["timeout"] is None

    def test_expired_timeout_raises_lpass_timeout_error(self) -> None:
        """subprocess.TimeoutExpired is translated into the library's error type."""
        exc = subprocess.TimeoutExpired(cmd=["lpass", "show"], timeout=60.0)
        with patch("subprocess.run", side_effect=exc):
            with pytest.raises(LpassTimeoutError) as excinfo:
                LpassClient("u@example.com").get_item(ITEM_NAME)
        assert excinfo.value.command == "show"
        assert excinfo.value.timeout == 60.0

    def test_timeout_on_the_write_path(self) -> None:
        """The stdin write path reports the edit sub-command when it times out."""
        exc = subprocess.TimeoutExpired(cmd=["lpass", "edit"], timeout=60.0)
        with patch("subprocess.run", side_effect=exc):
            with pytest.raises(LpassTimeoutError) as excinfo:
                LpassClient("u@example.com").create(ITEM_NAME, "svc", "s3cr3t")
        assert excinfo.value.command == "edit"

    def test_timeout_error_is_an_lpass_error(self) -> None:
        """LpassTimeoutError participates in the LpassError hierarchy."""
        assert issubclass(LpassTimeoutError, LpassError)

    def test_login_is_never_timed_out(self) -> None:
        """The interactive login waits on a human and must not carry a timeout."""
        with patch("subprocess.run", return_value=_make_proc(0)) as mock_run:
            LpassClient("u@example.com").login()
        assert mock_run.call_args.kwargs["timeout"] is None


class TestSyncOptIn:
    """Tests for the opt-in freshness flag on the cached field getters (S3)."""

    def _flags(self, mock_run: MagicMock) -> list[str]:
        """Return the command list from the single recorded subprocess call."""
        return list(mock_run.call_args.args[0])

    def test_get_field_defaults_to_cached_read(self) -> None:
        """Without sync=, get_field reads the local blob for speed."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="s3cr3t\n")) as mock_run:
            LpassClient("u@example.com").get_field(ITEM_NAME, "--password")
        assert "--sync=no" in self._flags(mock_run)

    def test_get_field_sync_true_forces_server_read(self) -> None:
        """sync=True upgrades the read to --sync=now."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="s3cr3t\n")) as mock_run:
            LpassClient("u@example.com").get_field(ITEM_NAME, "--password", sync=True)
        flags = self._flags(mock_run)
        assert "--sync=now" in flags
        assert "--sync=no" not in flags

    def test_get_password_forwards_sync(self) -> None:
        """get_password passes sync= through to get_field — the rotation case."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="s3cr3t\n")) as mock_run:
            LpassClient("u@example.com").get_password(ITEM_NAME, sync=True)
        assert "--sync=now" in self._flags(mock_run)

    def test_get_username_forwards_sync(self) -> None:
        """get_username passes sync= through to get_field."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="svc\n")) as mock_run:
            LpassClient("u@example.com").get_username(ITEM_NAME, sync=True)
        assert "--sync=now" in self._flags(mock_run)

    def test_get_password_defaults_to_cached_read(self) -> None:
        """The default stays --sync=no, so existing callers keep their speed."""
        with patch("subprocess.run", return_value=_make_proc(0, stdout="s3cr3t\n")) as mock_run:
            LpassClient("u@example.com").get_password(ITEM_NAME)
        assert "--sync=no" in self._flags(mock_run)
