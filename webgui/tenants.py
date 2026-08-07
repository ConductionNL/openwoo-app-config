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

# Frontend hosts on the platform domain are covered by the shared wildcard cert
# (`wildcard-openwoo-tls`, the ApplicationSet's default). Only a host OUTSIDE it
# needs a per-tenant `frontend.tls` block — emitting one for a platform host
# would replace a working wildcard with a secret nobody created.
PLATFORM_FRONTEND_DOMAIN = "openwoo.app"

# How a custom-domain frontend gets its certificate.
#   none            — bring your own: no cert-manager annotation, no Certificate
#                     object, so nothing can overwrite a customer-supplied cert.
#                     An operator seeds the Secret out of band (docs/custom-domain-cert.md).
#   letsencrypt-prod — cert-manager issues per host over HTTP-01.
# The ApplicationSet treats "none" and "absent" identically (it only writes the
# annotation `if and $tlsIssuer (ne $tlsIssuer "none")`); we still write "none"
# explicitly, because "nobody has decided yet" and "we deliberately bring our
# own" must not look the same in a tenant file.
TLS_ISSUERS = ("none", "letsencrypt-prod")
DEFAULT_TLS_ISSUER = "none"

# `<org>-<suffix>` with org a valid k8s-ish name segment. Matches the
# validate-values.sh convention (suffixes accept|test|demo|prod; test/demo -> accept).
_NAME_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])-(accept|test|demo|prod)$")
_SUFFIX_ENV = {"prod": "prod", "accept": "accept", "test": "accept", "demo": "accept"}


def filename(name):
    """Repo-relative path for a tenant's values file."""
    return f"tenant-{name}.yaml"


_ORG_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])$")
_RESERVED_SUFFIX = re.compile(r"-(accept|test|demo|prod)$")


def is_custom_frontend_host(host):
    """True when `host` needs its own certificate.

    A blank host means the platform derives `<org>.<env>.openwoo.app`, which the
    wildcard already covers — so blank is never custom.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    return not (host == PLATFORM_FRONTEND_DOMAIN
                or host.endswith("." + PLATFORM_FRONTEND_DOMAIN))


def tls_secret_name(host):
    """Derive the TLS Secret name for a custom frontend host.

    Follows the convention already in the fleet rather than inventing one:
    `acceptatie-open.oude-ijsselstreek.nl` -> `acceptatie-open-oude-ijsselstreek-nl-tls`
    (see Nextcloud-base tenant-oudeijsselstreek-accept.yaml). Dots become
    dashes; the name is a DNS-1123 label per Kubernetes' Secret naming rules.
    """
    host = (host or "").strip().lower().rstrip(".")
    return re.sub(r"[^a-z0-9-]", "-", host).strip("-") + "-tls"


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


def from_org(org, environment, dbType=None, display=None, host=None,
             theme=None, jumbotron=None, favicon=None, tls_issuer=None):
    """Build the full fields dict from the minimal input. Everything not given is
    derived: name=`<org>-<env>`, all three apps, branding 'Gemeente <Org>',
    db=postgres, host blank (=> platform derives <org>.<env>.commonground.nu).

    The branding extras (theme/jumbotron/favicon) default to blank on purpose —
    see render() for why a blank theme is the safe value."""
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
        "frontend_theme": (theme or "").strip(),
        "frontend_jumbotron": (jumbotron or "").strip(),
        "frontend_favicon": (favicon or "").strip(),
        "frontend_tls_issuer": (tls_issuer or "").strip() or DEFAULT_TLS_ISSUER,
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

    # Only meaningful for a custom host, but reject a bad value regardless: a
    # typo'd issuer silently becomes a cert-manager annotation nobody resolves.
    issuer = (fields.get("frontend_tls_issuer") or "").strip()
    if issuer and issuer not in TLS_ISSUERS:
        errors.append(f"frontend.tls.issuer must be one of {TLS_ISSUERS}")
    return errors


def _q(value):
    """Double-quote a scalar for YAML, escaping embedded quotes/backslashes."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(fields):
    """Render the tenant YAML as text. Assumes `validate(fields)` passed.

    Emits the minimal tenant block (name/environment/wave/dbType/apps) and an
    optional `frontend` block (host and/or branding) only when those fields are
    supplied — everything else is derived by the platform.

    Branding note. `frontend.branding` is read by react-base's `react-tenants`
    ApplicationSet, which turns each key into a `GATSBY_` env var on the
    frontend. `themeClassname` deserves care:

    - Blank is the safe value. The ApplicationSet falls back to
      `conduction-theme`, which ships with the bundled themes and renders out of
      the box. Deriving `<org>-theme` here would point at a theme that usually
      does not exist — exactly the bug react-base fixed on 2026-06-30, where
      onboarded tenants rendered without any theme.
    - Only set it when the tenant genuinely has its own bundled NL Design theme.
    - Creation is the moment that counts: the appset ignore-diffs the branding
      env (`^(GATSBY_|NL_DESIGN_)`) so devs can edit it live, which also means a
      value added to an *existing* tenant file does not reach a running
      frontend. A new tenant's frontend is created fresh, so what is declared
      here is what it starts with.

    TLS note. A `frontend.tls` block is emitted only for a host outside the
    platform domain; anything under `*.openwoo.app` is already covered by the
    shared wildcard, and overriding it would point the Ingress at a Secret
    nobody created. The Secret name is derived from the host, matching what the
    fleet already does — the certificate BYTES never travel through git or this
    portal, only the name does (see docs/custom-domain-cert.md)."""
    name = fields["name"].strip()
    env = fields["environment"].strip()
    wave = str(fields.get("wave") or "1").strip()
    db = fields["dbType"].strip()
    apps = list(fields["apps"])

    host = (fields.get("frontend_host") or "").strip()
    org = (fields.get("frontend_org") or "").strip()
    # Branding keys the react-tenants ApplicationSet turns into GATSBY_ env on the
    # frontend. Emitted only when supplied — see the branding note in render()'s
    # docstring for why a blank theme is the correct default.
    branding_extra = [
        ("themeClassname", (fields.get("frontend_theme") or "").strip()),
        ("jumbotronImageUrl", (fields.get("frontend_jumbotron") or "").strip()),
        ("faviconUrl", (fields.get("frontend_favicon") or "").strip()),
    ]
    extras = [(k, v) for k, v in branding_extra if v]

    lines = ["---", "tenant:", f"  name: {name}", f"  environment: {env}",
             f"  wave: {_q(wave)}", f"  dbType: {db}",
             # New-world tenants get ESO-managed secrets (generated in-cluster). The
             # flag gates charts/tenant-secret in the appset; existing tenants omit it.
             "  secrets:", "    managed: true",
             "  apps:", "    enabled:"]
    lines += [f"      - {a}" for a in apps]

    if host or org or extras:
        lines.append("  frontend:")
        if host:
            lines.append(f"    host: {host}")
        if is_custom_frontend_host(host):
            issuer = (fields.get("frontend_tls_issuer") or "").strip() or DEFAULT_TLS_ISSUER
            lines += ["    tls:",
                      f"      secretName: {tls_secret_name(host)}",
                      f"      issuer: {issuer}"]
        if org or extras:
            lines.append("    branding:")
            if org:
                lines.append(f"      organisationName: {_q(org)}")
            lines += [f"      {key}: {_q(value)}" for key, value in extras]

    return "\n".join(lines) + "\n"
