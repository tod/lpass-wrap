# Copyright 2026 Tod Detre
# SPDX-License-Identifier: GPL-3.0-or-later

"""Retrieve an Ansible Vault password from LastPass by vault identity label.

Intended for use as an Ansible vault password script via ``vault_identity_list``
in ``ansible.cfg``.  Ansible calls this script with ``--vault-id <label>`` and
the script prints the corresponding password to stdout, then exits 0.

Config file (``vault_pass_config.yml``) is located by:
  1. The ``VAULT_PASS_CONFIG`` environment variable (absolute path).
  2. ``vault_pass_config.yml`` in the current working directory.

Example ``ansible.cfg``::

    [defaults]
    vault_identity_list = gitlab@.venv/bin/get-ansible-vault-pass

Example ``vault_pass_config.yml``::

    lastpass_username: you@example.com
    vaults:
      gitlab: "3687030024433002683"
      ipa:    "9876543210123456789"
"""

import argparse
import os
import sys
from pathlib import Path

import structlog
import yaml

from lpass_wrap import LpassClient
from lpass_wrap._logging import setup_logging
from lpass_wrap.exceptions import LpassError

log = structlog.get_logger(__name__)

_CONFIG_ENV_VAR = "VAULT_PASS_CONFIG"
_CONFIG_FILENAME = "vault_pass_config.yml"


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser.

    Returns:
        Configured :class:`argparse.ArgumentParser` instance.
    """
    parser = argparse.ArgumentParser(
        description="Retrieve an Ansible Vault password from LastPass.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--vault-id",
        metavar="LABEL",
        default=None,
        help="Vault identity label passed automatically by Ansible.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity (-v INFO, -vv DEBUG).",
    )
    return parser


def _find_config() -> Path:
    """Locate the vault_pass_config.yml file.

    Checks ``$VAULT_PASS_CONFIG`` first, then falls back to ``vault_pass_config.yml``
    in the current working directory.

    Returns:
        Resolved path to the config file.

    Raises:
        SystemExit: If the config file cannot be found.
    """
    env_path = os.environ.get(_CONFIG_ENV_VAR)
    if env_path:
        p = Path(env_path)
        if not p.is_file():
            print(f"Config file not found at ${_CONFIG_ENV_VAR}={env_path}", file=sys.stderr)
            raise SystemExit(1)
        return p

    cwd_path = Path.cwd() / _CONFIG_FILENAME
    if cwd_path.is_file():
        return cwd_path

    print(
        f"Config file not found. Set ${_CONFIG_ENV_VAR} or place {_CONFIG_FILENAME!r} "
        "in the directory where you run ansible-playbook.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _load_config(config_path: Path) -> tuple[str, dict[str, str]]:
    """Parse vault_pass_config.yml and return (lastpass_username, vaults dict).

    Args:
        config_path: Path to the YAML config file.

    Returns:
        A tuple of ``(lastpass_username, {label: item_id, ...})``.

    Raises:
        SystemExit: If the config is missing required keys or malformed.
    """
    try:
        with config_path.open() as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        print(f"Cannot read config file {config_path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except yaml.YAMLError as exc:
        print(f"Malformed YAML in {config_path}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not isinstance(data, dict):
        print(f"Config file {config_path} must be a YAML mapping.", file=sys.stderr)
        raise SystemExit(1)

    username = data.get("lastpass_username")
    if not username:
        print(f"Config file {config_path} is missing 'lastpass_username'.", file=sys.stderr)
        raise SystemExit(1)

    vaults = data.get("vaults")
    if not isinstance(vaults, dict) or not vaults:
        print(f"Config file {config_path} must have a non-empty 'vaults' mapping.", file=sys.stderr)
        raise SystemExit(1)

    return str(username), {k: str(v) for k, v in vaults.items()}


def main() -> None:
    """Fetch the requested Ansible Vault password from LastPass and print it to stdout."""
    args = build_parser().parse_args()
    setup_logging(verbose=args.verbose)

    try:
        config_path = _find_config()
        log.debug("vault_pass_config_loaded", path=str(config_path))

        lastpass_username, vaults = _load_config(config_path)

        vault_id = args.vault_id
        if vault_id is None:
            if len(vaults) == 1:
                vault_id = next(iter(vaults))
            else:
                print(
                    "No --vault-id specified and multiple vaults are configured. "
                    "Ansible should pass --vault-id automatically.",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        if vault_id not in vaults:
            known = ", ".join(sorted(vaults))
            print(f"Unknown vault ID '{vault_id}'. Known labels: {known}", file=sys.stderr)
            raise SystemExit(1)

        item_id = vaults[vault_id]
        if item_id.upper().startswith("REPLACE"):
            print(
                f"Vault '{vault_id}' has a placeholder item ID in {config_path}. "
                "Update vault_pass_config.yml with the real LastPass item ID.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        client = LpassClient(username=lastpass_username)
        client.ensure_login()

        password = client.get_password(item_id)
        print(password)

    except LpassError as exc:
        print(f"LastPass error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
