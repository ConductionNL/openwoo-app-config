# SPDX-License-Identifier: EUPL-1.2
# Tests for scripts/cleanup-tenant.sh — the script is driven as a subprocess
# with a FAKE `kubectl` first on PATH, so nothing touches a cluster.
#
# The fake records every argv it is handed. That recording is the evidence for
# the two claims that matter most about this script: a plan run executes
# nothing, and an --execute run really does delete the namespace. Asserting on
# stdout alone could not tell those apart — the plan prints the same commands.
#
# bats is not a dependency of this repo (only vendored under hydra/), so these
# ride along in the existing pytest suite and `make test` picks them up.
"""Subprocess tests for scripts/cleanup-tenant.sh against a fake kubectl."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cleanup-tenant.sh"

# Answers only what the script actually asks. `get namespace` decides whether the
# tenant still exists; `get applications` feeds the Argo inventory; deletes just
# succeed. Every call is appended to $FAKE_KUBECTL_LOG, one argv per line.
_FAKE_KUBECTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_KUBECTL_LOG"
case " $* " in
  *" get namespace "*)
    if [ "${FAKE_NS_EXISTS:-0}" = "1" ]; then printf 'Active'; exit 0; fi
    exit 1 ;;
  *" get applications "*)
    if [ -n "${FAKE_APPS:-}" ]; then printf '%s\\n' "$FAKE_APPS"; fi
    exit 0 ;;
esac
exit 0
"""


@pytest.fixture
def kubectl(tmp_path):
    """Put a fake kubectl first on PATH and hand back a small driver."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "kubectl"
    fake.write_text(_FAKE_KUBECTL)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    log = tmp_path / "kubectl.log"
    log.write_text("")

    class _Driver:
        calls_file = log

        def run(self, *args, ns_exists=False, apps="", **env):
            environ = dict(os.environ)
            environ.update({
                "PATH": f"{bindir}:{environ['PATH']}",
                "FAKE_KUBECTL_LOG": str(log),
                "FAKE_NS_EXISTS": "1" if ns_exists else "0",
                "FAKE_APPS": apps,
            })
            # Never inherit the operator's own tuning into a test.
            for name in ("PROD_PATTERN", "PROD_TENANTS", "ARGO_NS"):
                environ.pop(name, None)
            environ.update({k: str(v) for k, v in env.items()})
            return subprocess.run([str(SCRIPT), *args], env=environ,
                                  capture_output=True, text=True, timeout=60)

        @property
        def calls(self):
            return [line for line in log.read_text().splitlines() if line]

    return _Driver()


def test_plan_only_touches_nothing(kubectl):
    """Without --execute the script must stay a document: it prints the delete
    commands but must not run one."""
    proc = kubectl.run("--tenant", "dryrun-accept",
                       ns_exists=True, apps="application.argoproj.io/nc-dryrun-accept")

    assert proc.returncode == 0
    # the plan is shown in full...
    assert "kubectl delete namespace dryrun-accept" in proc.stdout
    assert "Dit was een plan; er is niets gewijzigd." in proc.stdout
    # ...but nothing was marked as executed (run() prefixes executed commands
    # with an arrow) and no delete ever reached kubectl.
    assert "→" not in proc.stdout
    assert not [c for c in kubectl.calls if "delete" in c], kubectl.calls


def test_invalid_tenant_name_is_rejected(kubectl):
    proc = kubectl.run("--tenant", "Bad_Name!")
    assert proc.returncode == 2
    assert "ongeldige tenantnaam" in proc.stderr
    assert kubectl.calls == []          # rejected before any cluster call


def test_missing_tenant_argument_is_rejected(kubectl):
    proc = kubectl.run()
    assert proc.returncode == 2
    assert "geef --tenant" in proc.stderr


def test_production_name_needs_force(kubectl):
    """The default pattern guards the `-prod` convention."""
    blocked = kubectl.run("--tenant", "klant-prod", ns_exists=True)
    assert blocked.returncode == 2
    assert "geldt als productie" in blocked.stderr
    assert kubectl.calls == []          # refused before it looked at the cluster


def test_production_name_passes_the_valve_with_force(kubectl):
    allowed = kubectl.run("--tenant", "klant-prod", "--force-production",
                          ns_exists=True)
    assert allowed.returncode == 0
    assert "geldt als productie" not in allowed.stderr


def test_prod_tenants_env_widens_the_valve(kubectl):
    """A production tenant that does not follow the naming convention must be
    guardable from the environment — nothing about this valve is hardcoded."""
    unguarded = kubectl.run("--tenant", "klantnaam", ns_exists=True)
    assert unguarded.returncode == 0

    guarded = kubectl.run("--tenant", "klantnaam", ns_exists=True,
                          PROD_TENANTS="andere-klant klantnaam")
    assert guarded.returncode == 2
    assert "geldt als productie" in guarded.stderr


def test_prod_pattern_env_is_tunable(kubectl):
    proc = kubectl.run("--tenant", "klant-live", ns_exists=True,
                       PROD_PATTERN="-(live|prod)$")
    assert proc.returncode == 2
    assert "geldt als productie" in proc.stderr


def test_idempotent_when_everything_is_already_gone(kubectl):
    """Running it twice must be safe: with no namespace and no apps it reports
    that there is nothing to do and succeeds."""
    proc = kubectl.run("--tenant", "dryrun-accept", "--execute", "--yes",
                       ns_exists=False, apps="")
    assert proc.returncode == 0
    assert "Niets te doen" in proc.stdout
    assert not [c for c in kubectl.calls if "delete" in c], kubectl.calls


def test_execute_yes_deletes_the_namespace(kubectl):
    """The namespace delete is the step that actually stops the orphaned
    frontend from serving traffic, so prove it is issued."""
    proc = kubectl.run("--tenant", "dryrun-accept", "--execute", "--yes",
                       ns_exists=True, apps="application.argoproj.io/nc-dryrun-accept")

    assert proc.returncode == 0
    assert "delete namespace dryrun-accept" in kubectl.calls
    assert "-n argocd delete application.argoproj.io/nc-dryrun-accept" in kubectl.calls
    # executed commands are echoed with the arrow marker
    assert "→ kubectl delete namespace dryrun-accept" in proc.stdout


def test_execute_respects_argo_namespace_override(kubectl):
    proc = kubectl.run("--tenant", "dryrun-accept", "--execute", "--yes",
                       ns_exists=True, apps="application.argoproj.io/nc-dryrun-accept",
                       ARGO_NS="argo-system")
    assert proc.returncode == 0
    assert any(c.startswith("-n argo-system get applications") for c in kubectl.calls)


def test_help_documents_the_env_knobs(kubectl):
    proc = kubectl.run("--help")
    assert proc.returncode == 0
    assert "PROD_TENANTS" in proc.stdout
