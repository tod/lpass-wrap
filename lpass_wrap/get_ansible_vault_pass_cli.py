# Copyright 2026 Tod Detre
# SPDX-License-Identifier: Apache-2.0

"""Retrieve an Ansible Vault password from LastPass by vault identity label.

Intended for use as an Ansible vault password script via ``vault_identity_list``
in ``ansible.cfg``.  Ansible calls this script with ``--vault-id <label>`` and
the script prints the corresponding password to stdout, then exits 0.

Config file (``vault_pass_config.yml``) is located by:
  1. The ``VAULT_PASS_CONFIG`` environment variable (absolute path).
  2. ``vault_pass_config.yml`` in the current working directory.

Example ``ansible.cfg``::

    [defaults]
    vault_identity_list = mylabel@.venv/bin/get-ansible-vault-pass-client

Example ``vault_pass_config.yml``::

    lastpass_username: you@example.com
    vaults:
      mylabel: "1111111111111111111"
      other:   "2222222222222222222"
"""

import os
from pathlib import Path
from typing import Annotated, Optional

import structlog
import typer
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lpass_wrap import LpassClient
from lpass_wrap._logging import setup_logging
from lpass_wrap.exceptions import LpassError

log = structlog.get_logger(__name__)

_CONFIG_ENV_VAR = "VAULT_PASS_CONFIG"
_CONFIG_FILENAME = "vault_pass_config.yml"

app = typer.Typer(add_completion=False)


class VaultPassConfig(BaseModel):
    """Validated contents of ``vault_pass_config.yml``.

    Numeric vault item IDs are coerced to strings, so unquoted YAML integers
    (easy to produce by hand-editing) parse the same as quoted ones.

    Example::

        config = VaultPassConfig.model_validate(yaml.safe_load(text))
        config.vaults["mylabel"]  # "1111111111111111111"
    """

    model_config = ConfigDict(frozen=True, coerce_numbers_to_str=True)

    lastpass_username: str = Field(min_length=1, description="LastPass account email used for lpass login.")
    vaults: dict[str, str] = Field(min_length=1, description="Mapping of vault identity label to LastPass item ID.")


def _fail(message: str) -> typer.Exit:
    """Print an error message to stderr and return an exit-1 exception to raise.

    Args:
        message: Human-readable error text for the operator.

    Returns:
        A :class:`typer.Exit` carrying exit code 1, for the caller to raise.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


def _find_config(env_path: str | None) -> Path:
    """Locate the vault_pass_config.yml file.

    Checks ``$VAULT_PASS_CONFIG`` first, then falls back to
    ``vault_pass_config.yml`` in the current working directory.

    Args:
        env_path: Value of ``$VAULT_PASS_CONFIG``, or None if unset.

    Returns:
        Resolved path to the config file.

    Raises:
        typer.Exit: If the config file cannot be found (exit code 1).
    """
    if env_path:
        p = Path(env_path)
        if not p.is_file():
            raise _fail(f"Config file not found at ${_CONFIG_ENV_VAR}={env_path}")
        return p

    cwd_path = Path.cwd() / _CONFIG_FILENAME
    if cwd_path.is_file():
        return cwd_path

    raise _fail(
        f"Config file not found. Set ${_CONFIG_ENV_VAR} or place {_CONFIG_FILENAME!r} "
        "in the directory where you run ansible-playbook."
    )


def _load_config(config_path: Path) -> VaultPassConfig:
    """Parse and validate vault_pass_config.yml.

    Args:
        config_path: Path to the YAML config file.

    Returns:
        The validated :class:`VaultPassConfig`.

    Raises:
        typer.Exit: If the config cannot be read, is malformed YAML, or fails
            validation (exit code 1).
    """
    try:
        with config_path.open() as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise _fail(f"Cannot read config file {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise _fail(f"Malformed YAML in {config_path}: {exc}") from exc

    try:
        return VaultPassConfig.model_validate(data)
    except ValidationError as exc:
        raise _fail(f"Invalid config file {config_path}:\n{exc}") from exc


def _resolve_vault_id(vault_id: str | None, config: VaultPassConfig, config_path: Path) -> str:
    """Resolve the vault label to look up, validating it against the config.

    Args:
        vault_id:    The ``--vault-id`` value, or None if Ansible did not pass one.
        config:      The validated config.
        config_path: Path to the config file (for error messages).

    Returns:
        The LastPass item ID for the requested vault.

    Raises:
        typer.Exit: If the label is missing/unknown or maps to a placeholder
            item ID (exit code 1).
    """
    if vault_id is None:
        if len(config.vaults) == 1:
            vault_id = next(iter(config.vaults))
        else:
            raise _fail(
                "No --vault-id specified and multiple vaults are configured. "
                "Ansible should pass --vault-id automatically."
            )

    if vault_id not in config.vaults:
        known = ", ".join(sorted(config.vaults))
        raise _fail(f"Unknown vault ID '{vault_id}'. Known labels: {known}")

    item_id = config.vaults[vault_id]
    if item_id.upper().startswith("REPLACE"):
        raise _fail(
            f"Vault '{vault_id}' has a placeholder item ID in {config_path}. "
            "Update vault_pass_config.yml with the real LastPass item ID."
        )
    return item_id


@app.command()
def cli(
    vault_id: Annotated[
        Optional[str],
        typer.Option(
            "--vault-id",
            metavar="LABEL",
            help="Vault identity label passed automatically by Ansible.",
        ),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Increase log verbosity (-v INFO, -vv DEBUG).",
        ),
    ] = 0,
) -> None:
    """Retrieve an Ansible Vault password from LastPass and print it to stdout."""
    setup_logging(verbose=verbose)

    try:
        config_path = _find_config(os.environ.get(_CONFIG_ENV_VAR))
        log.debug("vault_pass_config_loaded", path=str(config_path))

        config = _load_config(config_path)
        item_id = _resolve_vault_id(vault_id, config, config_path)

        client = LpassClient(username=config.lastpass_username)
        client.ensure_login()

        password = client.get_password(item_id)
        typer.echo(password)

    except LpassError as exc:
        typer.echo(f"LastPass error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        typer.echo("\nAborted.", err=True)
        raise SystemExit(130) from None


def main() -> None:
    """Console-script entry point; runs the typer application."""
    app()


if __name__ == "__main__":
    main()
