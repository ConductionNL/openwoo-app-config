# SPDX-License-Identifier: EUPL-1.2
"""Unit tests for the Tkinter front-end's command builder (no GUI needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import provision_gui  # noqa: E402


def test_build_command_secrets_via_env_nonsecrets_via_argv():
    argv, env = provision_gui.build_command({
        "base": "https://klant.accept.commonground.nu",
        "user": "admin",
        "password": "s3cr3t",
        "source_url": "https://bron.example/api",
        "api_interface_id": "99",
        "apikey": "KEY123",
    })
    # non-secrets on argv
    assert "--base" in argv and "https://klant.accept.commonground.nu" in argv
    assert argv[argv.index("--user") + 1] == "admin"
    assert argv[argv.index("--source-url") + 1] == "https://bron.example/api"
    assert argv[argv.index("--api-interface-id") + 1] == "99"
    # secrets referenced by env, NOT present as argv values
    assert "s3cr3t" not in argv and "KEY123" not in argv
    assert env[argv[argv.index("--password-env") + 1]] == "s3cr3t"
    assert env[argv[argv.index("--apikey-env") + 1]] == "KEY123"


def test_build_command_omits_blank_optionals():
    argv, _env = provision_gui.build_command({
        "base": "https://k.accept.commonground.nu", "user": "admin",
        "password": "", "source_url": "", "api_interface_id": "", "apikey": "",
    })
    for absent in ("--source-url", "--api-interface-id", "--password-env", "--apikey-env"):
        assert absent not in argv


def test_build_command_job_user_optional():
    base = {"base": "https://k.accept.commonground.nu", "user": "admin"}
    argv, _ = provision_gui.build_command({**base, "job_user": "admin"})
    assert argv[argv.index("--job-user") + 1] == "admin"
    argv, _ = provision_gui.build_command(base)
    assert "--job-user" not in argv


def test_build_command_run_syncs_real_and_dry():
    base = {"base": "https://k.accept.commonground.nu", "user": "admin"}
    argv, _ = provision_gui.build_command({**base, "run_syncs": True})
    assert "--run-syncs" in argv and "--test" not in argv          # real run
    argv, _ = provision_gui.build_command({**base, "run_syncs": True, "dry_run": True})
    assert "--run-syncs" in argv and "--test" in argv              # dry-run
    argv, _ = provision_gui.build_command({**base, "run_syncs": False, "dry_run": True})
    assert "--run-syncs" not in argv and "--test" not in argv      # dry-run only matters with run-syncs


def test_build_command_requires_base():
    for bad in ("", "   ", "https://<org>.accept.commonground.nu"):
        with pytest.raises(ValueError, match="base URL is required"):
            provision_gui.build_command({"base": bad})
