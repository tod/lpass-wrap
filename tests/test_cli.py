# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the get-ansible-vault-pass-client CLI.

Exercises the typer command through CliRunner with LpassClient mocked, so
lpass does not need to be installed.  The exit-code contract matters here:
Ansible treats any non-zero exit as "no password", so 0 must mean a password
was printed to stdout and nothing else was.

All vault labels and item IDs in this file are synthetic.
"""

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner, Result

from lpass_wrap.exceptions import LpassError
from lpass_wrap.get_ansible_vault_pass_cli import app

runner = CliRunner()

FAKE_ID_ALPHA = "1111111111111111111"
FAKE_ID_BETA = "2222222222222222222"

VALID_CONFIG = f"""\
lastpass_username: you@example.com
vaults:
  alpha: "{FAKE_ID_ALPHA}"
  beta: "{FAKE_ID_BETA}"
"""


def _write_config(tmp_path: Path, content: str = VALID_CONFIG) -> Path:
    """Write a config file into tmp_path and return its path.

    Args:
        tmp_path: pytest-provided temporary directory.
        content:  YAML text to write.

    Returns:
        Path to the written config file.
    """
    config = tmp_path / "vault_pass_config.yml"
    config.write_text(content)
    return config


def _invoke(config_path: Path | None, *args: str) -> Result:
    """Invoke the CLI with VAULT_PASS_CONFIG pointed at the given config.

    Args:
        config_path: Config file path for the env var, or None to leave unset.
        *args:       CLI arguments.

    Returns:
        The CliRunner result.
    """
    env = {"VAULT_PASS_CONFIG": str(config_path)} if config_path else {}
    return runner.invoke(app, list(args), env=env)


class TestHelp:
    """Smoke tests for CLI wiring."""

    def test_help_exits_zero(self) -> None:
        """--help exits 0 and mentions the --vault-id option."""
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        assert "--vault-id" in plain

    def test_unknown_option_exits_two(self) -> None:
        """An unknown option is a usage error (exit 2), matching argparse."""
        result = runner.invoke(app, ["--bogus"])
        assert result.exit_code == 2


class TestConfigDiscovery:
    """Tests for config file location and readability errors."""

    def test_env_var_missing_file_exits_one(self, tmp_path: Path) -> None:
        """A VAULT_PASS_CONFIG pointing at a missing file exits 1."""
        result = _invoke(tmp_path / "nope.yml", "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "not found" in result.stderr

    def test_no_env_no_cwd_file_exits_one(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no env var and no config in cwd, exits 1 with guidance."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["--vault-id", "alpha"], env={"VAULT_PASS_CONFIG": ""})
        assert result.exit_code == 1
        assert "VAULT_PASS_CONFIG" in result.stderr

    def test_cwd_fallback_is_used(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """vault_pass_config.yml in the cwd is found without the env var."""
        _write_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client_cls.return_value.get_password.return_value = "s3cr3t"
            result = runner.invoke(app, ["--vault-id", "alpha"], env={"VAULT_PASS_CONFIG": ""})
        assert result.exit_code == 0
        assert result.stdout == "s3cr3t\n"


class TestConfigValidation:
    """Tests for pydantic validation of the config file."""

    def test_malformed_yaml_exits_one(self, tmp_path: Path) -> None:
        """Unparseable YAML exits 1."""
        config = _write_config(tmp_path, "lastpass_username: [unclosed")
        result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "Malformed YAML" in result.stderr

    def test_non_mapping_exits_one(self, tmp_path: Path) -> None:
        """A YAML document that is not a mapping exits 1."""
        config = _write_config(tmp_path, "- just\n- a\n- list\n")
        result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "Invalid config" in result.stderr

    def test_missing_username_exits_one(self, tmp_path: Path) -> None:
        """A config without lastpass_username exits 1."""
        config = _write_config(tmp_path, 'vaults:\n  alpha: "123"\n')
        result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "lastpass_username" in result.stderr

    def test_empty_vaults_exits_one(self, tmp_path: Path) -> None:
        """A config with an empty vaults mapping exits 1."""
        config = _write_config(tmp_path, "lastpass_username: you@example.com\nvaults: {}\n")
        result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "vaults" in result.stderr

    def test_unquoted_integer_item_id_is_coerced(self, tmp_path: Path) -> None:
        """An unquoted numeric item ID parses as its string form."""
        config = _write_config(
            tmp_path,
            f"lastpass_username: you@example.com\nvaults:\n  alpha: {FAKE_ID_ALPHA}\n",
        )
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client_cls.return_value.get_password.return_value = "s3cr3t"
            result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 0
        client_cls.return_value.get_password.assert_called_once_with(FAKE_ID_ALPHA)


class TestVaultIdResolution:
    """Tests for --vault-id handling."""

    def test_unknown_vault_id_exits_one(self, tmp_path: Path) -> None:
        """An unknown label exits 1 and lists the known labels."""
        config = _write_config(tmp_path)
        result = _invoke(config, "--vault-id", "nope")
        assert result.exit_code == 1
        assert "alpha" in result.stderr and "beta" in result.stderr

    def test_no_vault_id_multiple_vaults_exits_one(self, tmp_path: Path) -> None:
        """Omitting --vault-id with multiple vaults configured exits 1."""
        config = _write_config(tmp_path)
        result = _invoke(config)
        assert result.exit_code == 1
        assert "multiple vaults" in result.stderr

    def test_no_vault_id_single_vault_defaults(self, tmp_path: Path) -> None:
        """Omitting --vault-id with exactly one vault uses that vault."""
        config = _write_config(tmp_path, 'lastpass_username: you@example.com\nvaults:\n  only: "111"\n')
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client_cls.return_value.get_password.return_value = "pw"
            result = _invoke(config)
        assert result.exit_code == 0
        assert result.stdout == "pw\n"

    def test_placeholder_item_id_exits_one(self, tmp_path: Path) -> None:
        """A REPLACE_ME item ID exits 1 with a pointer to the config."""
        config = _write_config(tmp_path, "lastpass_username: you@example.com\nvaults:\n  alpha: REPLACE_ME\n")
        result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "placeholder" in result.stderr


class TestLpassInteraction:
    """Tests for the happy path and lpass failure handling."""

    def test_happy_path_prints_only_password_to_stdout(self, tmp_path: Path) -> None:
        """Exit 0 with exactly the password on stdout — the Ansible contract."""
        config = _write_config(tmp_path)
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client = client_cls.return_value
            client.get_password.return_value = "s3cr3t"
            result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 0
        assert result.stdout == "s3cr3t\n"
        client_cls.assert_called_once_with(username="you@example.com")
        client.ensure_login.assert_called_once_with()
        client.get_password.assert_called_once_with(FAKE_ID_ALPHA)

    def test_lpass_error_exits_one(self, tmp_path: Path) -> None:
        """Any LpassError from the client exits 1 with the message on stderr."""
        config = _write_config(tmp_path)
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client_cls.return_value.ensure_login.side_effect = LpassError("session expired")
            result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 1
        assert "LastPass error: session expired" in result.stderr
        assert result.stdout == ""

    def test_keyboard_interrupt_exits_130(self, tmp_path: Path) -> None:
        """Ctrl-C during the lpass interaction exits 130."""
        config = _write_config(tmp_path)
        with patch("lpass_wrap.get_ansible_vault_pass_cli.LpassClient") as client_cls:
            client_cls.return_value.ensure_login.side_effect = KeyboardInterrupt()
            result = _invoke(config, "--vault-id", "alpha")
        assert result.exit_code == 130
        assert "Aborted" in result.stderr
