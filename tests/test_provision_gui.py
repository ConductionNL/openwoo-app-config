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


def test_build_command_force_import():
    base = {"base": "https://k.accept.commonground.nu", "user": "admin"}
    argv, _ = provision_gui.build_command({**base, "force_import": True})
    assert "--force-import" in argv
    argv, _ = provision_gui.build_command(base)
    assert "--force-import" not in argv


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


def test_incluster_target_prod_and_env():
    # prod tenant: <org>.commonground.nu -> ns <org>-prod
    assert provision_gui.incluster_target("https://noorderzijlvest.commonground.nu") == (
        "http://nextcloud.noorderzijlvest-prod.svc.cluster.local:8080",
        "noorderzijlvest.commonground.nu",
    )
    # non-prod tenant: <org>.<env>.commonground.nu -> ns <org>-<env>
    assert provision_gui.incluster_target("https://klant.accept.commonground.nu") == (
        "http://nextcloud.klant-accept.svc.cluster.local:8080",
        "klant.accept.commonground.nu",
    )


def test_incluster_target_non_tenant_host_returns_none():
    for other in ("https://bron.example/api", "http://localhost:8080",
                  "https://a.b.c.commonground.nu"):
        assert provision_gui.incluster_target(other) is None


def test_build_command_in_cluster_rewrites_base_and_adds_host_header():
    argv, env = provision_gui.build_command({
        "base": "https://noorderzijlvest.commonground.nu",
        "user": "admin", "password": "s3cr3t", "in_cluster": True,
    })
    # connect to the internal Service, present the public host
    assert argv[argv.index("--base") + 1] == \
        "http://nextcloud.noorderzijlvest-prod.svc.cluster.local:8080"
    assert argv[argv.index("--host-header") + 1] == "noorderzijlvest.commonground.nu"
    # secrets still only via env
    assert "s3cr3t" not in argv
    assert env[argv[argv.index("--password-env") + 1]] == "s3cr3t"


def test_build_command_in_cluster_off_keeps_public_base():
    argv, _ = provision_gui.build_command({
        "base": "https://noorderzijlvest.commonground.nu", "user": "admin",
    })
    assert argv[argv.index("--base") + 1] == "https://noorderzijlvest.commonground.nu"
    assert "--host-header" not in argv


def test_build_command_in_cluster_non_tenant_host_falls_back():
    # in_cluster requested but host isn't a tenant host -> unchanged public base
    argv, _ = provision_gui.build_command({
        "base": "https://custom.example.org", "user": "admin", "in_cluster": True,
    })
    assert argv[argv.index("--base") + 1] == "https://custom.example.org"
    assert "--host-header" not in argv
