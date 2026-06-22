#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/tenants.py — render + validate a Nextcloud-base tenant file from form
# input, with NO third-party YAML dependency (the file is emitted as text).
#
# The validation mirrors Nextcloud-base `scripts/validate-values.sh` so the
# portal never opens a PR that the repo's CI would reject: name must be
# `<org>-<accept|test|demo|prod>`, environment must match the suffix, dbType is
# one of mariadb|postgres|external, and at least one app is enabled. Keeping the
# rules in lockstep with the validator is the contract (see that script).
#
# Writes: read-only (pure functions returning strings/lists).
# Requires: python3.8+ (stdlib `re` only).
"""Pure render/validate helpers for Nextcloud-base tenant files."""

import re

ENVS = ("accept", "prod")
DB_TYPES = ("mariadb", "postgres", "external")
KNOWN_APPS = ("opencatalogi", "openconnector", "openregister")

# `<org>-<suffix>` with org a valid k8s-ish name segment. Matches the
# validate-values.sh convention (suffixes accept|test|demo|prod; test/demo -> accept).
_NAME_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])-(accept|test|demo|prod)$")
_SUFFIX_ENV = {"prod": "prod", "accept": "accept", "test": "accept", "demo": "accept"}


def filename(name):
    """Repo-relative path for a tenant's values file."""
    return f"tenant-{name}.yaml"


_ORG_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])$")
_RESERVED_SUFFIX = re.compile(r"-(accept|test|demo|prod)$")


def org_display(org):
    """Default WOO branding name (PO convention): 'Gemeente <Org>'."""
    parts = [p for p in (org or "").replace("_", "-").split("-") if p]
    return "Gemeente " + " ".join(p.capitalize() for p in parts)


def validate_org(org, environment):
    """Validate the minimal operator input (bare org + environment). The full
    tenant name is DERIVED (`<org>-<env>`), so the operator never types it."""
    org = (org or "").strip()
    errs = []
    if not org:
        errs.append("organisation is required")
    elif _RESERVED_SUFFIX.search(org):
        errs.append("give the bare organisation (e.g. 'almere'), not '<org>-<env>' "
                    "— the environment is the dropdown")
    elif not _ORG_RE.match(org):
        errs.append("organisation must be lowercase letters/digits/hyphens "
                    "(e.g. 'almere', 'oude-ijsselstreek')")
    if environment not in ENVS:
        errs.append(f"environment must be one of {ENVS}")
    return errs


def from_org(org, environment, dbType=None, display=None, host=None):
    """Build the full fields dict from the minimal input. Everything not given is
    derived: name=`<org>-<env>`, all three apps, branding 'Gemeente <Org>',
    db=postgres, host blank (=> platform derives <org>.<env>.commonground.nu)."""
    org = (org or "").strip().lower()
    env = (environment or "").strip()
    disp = (display or "").strip() or (org_display(org) if org else "")
    return {
        "name": f"{org}-{env}" if (org and env) else "",
        "environment": env,
        "dbType": (dbType or "").strip() or "postgres",
        "wave": "1",
        "apps": list(KNOWN_APPS),
        "frontend_org": disp,
        "frontend_host": (host or "").strip(),
    }


def validate(fields):
    """Return a list of human-readable error strings ([] == valid).

    `fields` keys: name, environment, dbType, apps (list[str]); optional wave.
    Mirrors validate-values.sh so a valid result here passes Nextcloud-base CI."""
    errors = []
    name = (fields.get("name") or "").strip()
    env = (fields.get("environment") or "").strip()
    db = (fields.get("dbType") or "").strip()
    apps = fields.get("apps") or []

    m = _NAME_RE.match(name)
    if not m:
        errors.append("name must be '<org>-<accept|test|demo|prod>' (lowercase, "
                      "e.g. 'almere-accept')")
    else:
        suffix = m.group(2)
        expected_env = _SUFFIX_ENV[suffix]
        if env not in ENVS:
            errors.append(f"environment must be one of {ENVS}")
        elif env != expected_env:
            errors.append(f"environment must be '{expected_env}' for a '-{suffix}' "
                          f"tenant (got '{env}')")

    if db not in DB_TYPES:
        errors.append(f"dbType must be one of {DB_TYPES}")

    if not apps:
        errors.append("at least one app must be enabled")
    else:
        unknown = [a for a in apps if a not in KNOWN_APPS]
        if unknown:
            errors.append(f"unknown app(s): {', '.join(unknown)} "
                          f"(known: {', '.join(KNOWN_APPS)})")
    return errors


def _q(value):
    """Double-quote a scalar for YAML, escaping embedded quotes/backslashes."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(fields):
    """Render the tenant YAML as text. Assumes `validate(fields)` passed.

    Emits the minimal tenant block (name/environment/wave/dbType/apps) and an
    optional `frontend` block (host and/or branding.organisationName) only when
    those fields are supplied — everything else is derived by the platform."""
    name = fields["name"].strip()
    env = fields["environment"].strip()
    wave = str(fields.get("wave") or "1").strip()
    db = fields["dbType"].strip()
    apps = list(fields["apps"])

    host = (fields.get("frontend_host") or "").strip()
    org = (fields.get("frontend_org") or "").strip()

    lines = ["---", "tenant:", f"  name: {name}", f"  environment: {env}",
             f"  wave: {_q(wave)}", f"  dbType: {db}",
             # New-world tenants get ESO-managed secrets (generated in-cluster). The
             # flag gates charts/tenant-secret in the appset; existing tenants omit it.
             "  secrets:", "    managed: true",
             "  apps:", "    enabled:"]
    lines += [f"      - {a}" for a in apps]

    if host or org:
        lines.append("  frontend:")
        if host:
            lines.append(f"    host: {host}")
        if org:
            lines += ["    branding:", f"      organisationName: {_q(org)}"]

    return "\n".join(lines) + "\n"
