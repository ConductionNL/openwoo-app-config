#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/provision.py — post-import tenant provisioning steps over the API.
#
# After a config is imported (scripts/functional-test.sh, import step), a real
# tenant bring-up still needs a few API-driven steps. This tool performs the
# ones that the WOO configuration itself owns and asserts they took effect —
# "test what you ship". It is driven by the same sanitized config the import
# uses, so the entities it touches are exactly the ones the config defines.
#
# Scope (WOO-config-owned, verified against a live stack):
#   credentials  — for every source in the config, resolve it by slug on the
#                  running instance, PUT a dummy apikey, then GET it back and
#                  assert the apikey reflects. Proves the credential-provisioning
#                  path works without performing any real data fetch (the demo
#                  source is auth:none; we never store a real secret here — real
#                  credentials come from a K8s secret / ESO, never the config).
#
# Deliberately NOT here: OpenCatalogi settings / default-catalog / home-page
# steps. Those operate on OpenCatalogi's *own* entities (a `publication`
# register, `catalog`/`listing`/... schemas) which are NOT in the WOO config and
# do not exist on an instance that imported only this config (verified: a fresh
# import yields register `woo` + 17 WOO schemas and no `publication` register).
# They belong to a separate OpenCatalogi-base provisioning flow — see
# docs/PROVISIONING-TEST-PLAN.md.
#
# Auth: basic-auth (admin user / app password). The source list + update routes
# (GET/PUT /apps/openconnector/api/sources[/{id}]) accept basic-auth and need no
# CSRF requesttoken, verified against openconnector 0.2.20. The OpenRegister
# registers/schemas *list* routes do require a browser CSRF session and are not
# used here.
#
# Pure Python standard library — no third-party dependencies, by design
# (auditability + no supply-chain surface). Mirrors scripts/oac.py.
#
# Writes: read-only on the repo; mutates the *running test instance* (source
#         apikey). Intended for the ephemeral functional-test stack.
# Idempotent: yes — re-running sets the same dummy key and re-asserts.
# Requires: python3.8+, a running instance reachable at --base.
#
# Usage:
#   python3 scripts/provision.py credentials \
#       --base http://localhost:8080 --user admin --password admin_test_only
#   python3 scripts/provision.py credentials --base ... --user ... --password ... \
#       --config config/woo.configuration.json
"""Post-import tenant provisioning steps over the OpenRegister/OpenConnector API."""

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The xxllnc / OpenWoo source authenticates with an API key sent as a request
# header; OpenConnector stores per-source headers as dotted keys inside the
# source `configuration` object (e.g. "headers.API-KEY"). The real key is never
# committed — it is injected here at provision time from --apikey / --apikey-env
# (a K8s secret / ESO in prod). The config ships an empty placeholder.
API_KEY_HEADER = "headers.API-KEY"

# Dummy apikey marker. Clearly not a real secret; safe to log. When no real key
# is supplied (the CI / local functional test), the credentials step writes
# "<prefix><slug>" so the assertion is per-source and deterministic.
DUMMY_APIKEY_PREFIX = "DUMMY-PROVISION-TEST-"


# --- Pure helpers (unit-tested without a live stack) ---


def load_config(path):
    """Load the configuration JSON document."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def bucket_items(doc, bucket):
    """Return a bucket's entities as a list, whether stored as list or dict."""
    raw = doc.get("components", {}).get(bucket)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return list(raw.values())
    return []


def config_source_slugs(doc):
    """Slugs of every source defined in the config (skip entries without one)."""
    return [s["slug"] for s in bucket_items(doc, "sources") if s.get("slug")]


def results_list(payload):
    """Unwrap a list endpoint response: {"results": [...]} or a bare list."""
    if isinstance(payload, dict):
        inner = payload.get("results", [])
        return inner if isinstance(inner, list) else []
    return payload if isinstance(payload, list) else []


def find_by_slug(items, slug):
    """First item whose slug (or, as a fallback, name) matches; else None."""
    for item in items:
        if item.get("slug") == slug:
            return item
    for item in items:
        if item.get("name") == slug:
            return item
    return None


def dummy_apikey(slug):
    """Deterministic, obviously-fake apikey for a source slug."""
    return f"{DUMMY_APIKEY_PREFIX}{slug}"


# --- Thin HTTP client (basic-auth, JSON) ---


class Client:
    """Minimal Nextcloud API client: basic-auth + OCS-APIREQUEST, JSON in/out."""

    def __init__(self, base, user, password):
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth_header = f"Basic {token}"

    def _request(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth_header)
        req.add_header("OCS-APIREQUEST", "true")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ProvisionError(
                f"{method} {path} -> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProvisionError(f"{method} {path} -> {exc.reason}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # A login/HTML page instead of JSON means the route refused our auth.
            raise ProvisionError(
                f"{method} {path} -> non-JSON response (auth refused?): {raw[:120]}"
            )

    def get(self, path):
        return self._request("GET", path)

    def put(self, path, body):
        return self._request("PUT", path, body)

    def post(self, path, body=None):
        return self._request("POST", path, body)


class ProvisionError(Exception):
    """A provisioning step failed (HTTP error, refused auth, or failed assert)."""


# --- Commands ---

SOURCES_PATH = "/index.php/apps/openconnector/api/sources"
SYNCS_PATH = "/index.php/apps/openconnector/api/synchronizations"
REGISTERS_PATH = "/index.php/apps/openregister/api/registers"
SCHEMAS_PATH = "/index.php/apps/openregister/api/schemas"

# Config bucket -> the tenant list endpoint that should hold its rows after
# import. Used by verify_import to compare config slugs against what is actually
# present on the tenant (a slug-level check the bulk row count cannot give).
VERIFY_BUCKETS = {
    "registers": REGISTERS_PATH,
    "schemas": SCHEMAS_PATH,
    "sources": SOURCES_PATH,
    "synchronizations": SYNCS_PATH,
}


def config_slugs(doc, bucket):
    """Slugs of every entity in a config bucket (skip entries without one)."""
    return [e["slug"] for e in bucket_items(doc, bucket) if e.get("slug")]


def verify_import(client, doc):
    """Compare config slugs to what is present on the tenant, per bucket.

    Returns {bucket: {"expected": n, "missing": [slugs...]}}. A non-empty
    `missing` means the import silently dropped those entities (the import API
    returns HTTP 200 even when it omits rows), which the bulk row count cannot
    catch on a tenant that already held data.
    """
    report = {}
    for bucket, path in VERIFY_BUCKETS.items():
        want = config_slugs(doc, bucket)
        if not want:
            continue
        present = {item.get("slug") for item in results_list(client.get(path))}
        report[bucket] = {
            "expected": len(want),
            "missing": [s for s in want if s not in present],
        }
    return report


SETTINGS_ORG_PATH = "/index.php/apps/openregister/api/settings/organisation"
SETTINGS_MT_PATH = "/index.php/apps/openregister/api/settings/multitenancy"


def put_settings_reflected(client, path, key, payload):
    """PUT a settings payload, GET it back (unwrapping `key`), assert it reflects.

    These endpoints return the saved values wrapped under a single key
    (`{"organisation": {...}}` / `{"multitenancy": {...}}`); we only assert the
    fields we sent, so extra server-side fields (e.g. an auto-created org id) are
    fine.
    """
    client.put(path, payload)
    got = client.get(path).get(key, {})
    mismatched = {k: got.get(k) for k, v in payload.items() if got.get(k) != v}
    if mismatched:
        raise ProvisionError(
            f"{path}: settings did not reflect (sent {payload}, got back {mismatched})"
        )
    return got


def provision_settings(client, organisation, multitenancy):
    """PUT the organisation and multitenancy settings and assert both reflect."""
    log("  PUT settings/organisation")
    org = put_settings_reflected(client, SETTINGS_ORG_PATH, "organisation", organisation)
    log("  PUT settings/multitenancy")
    mt = put_settings_reflected(client, SETTINGS_MT_PATH, "multitenancy", multitenancy)
    return {"organisation": org, "multitenancy": mt}


def target_schema_resolved(target_id):
    """A sync targetId is "register/schema"; resolved iff the schema part is numeric.

    After import the schema slug should be rewritten to its numeric id (e.g.
    "2/19"). A leftover slug (e.g. "2/convenanten") means the target schema was
    never created, so the synchronization is dangling.
    """
    if not isinstance(target_id, str) or "/" not in target_id:
        return False
    _register, _, schema = target_id.partition("/")
    return schema.isdigit()


def sync_check(client, doc):
    """Find synchronizations on the tenant whose target schema did not resolve.

    Returns {"total": n, "dangling": [{"slug":..., "targetId":...}, ...]}.
    Cross-checks against the config sync slugs so we only report the ones this
    config is responsible for.
    """
    want = set(config_slugs(doc, "synchronizations"))
    syncs = results_list(client.get(SYNCS_PATH))
    dangling = []
    for s in syncs:
        if want and s.get("slug") not in want:
            continue
        if not target_schema_resolved(s.get("targetId")):
            dangling.append({"slug": s.get("slug"), "targetId": s.get("targetId")})
    return {"total": len(syncs), "dangling": dangling}


def merge_header(configuration, header, value):
    """Return the source configuration with `header` set, preserving the rest.

    OpenConnector serializes an empty configuration as a list; treat any
    non-dict as empty so we never clobber existing headers on PUT.
    """
    base = dict(configuration) if isinstance(configuration, dict) else {}
    base[header] = value
    return base


def provision_credentials(client, doc, apikey=None, header=API_KEY_HEADER):
    """Set every config source's API-key header, GET it back, assert it stuck.

    `apikey` is the real key (from --apikey / --apikey-env); when None a per-slug
    dummy is used so the path is still exercised in CI without a secret. The key
    is merged into the source `configuration` (read-modify-write) so existing
    headers like API-Interface-ID are preserved.

    Returns the number of sources provisioned. Raises ProvisionError on the first
    hard failure (missing source, refused auth, key did not reflect).
    """
    slugs = config_source_slugs(doc)
    if not slugs:
        log("no sources in config — nothing to provision")
        return 0

    using_dummy = apikey is None
    log(f"key source: {'dummy (test mode)' if using_dummy else 'supplied key'}")
    sources = results_list(client.get(SOURCES_PATH))
    done = 0
    for slug in slugs:
        source = find_by_slug(sources, slug)
        if source is None:
            raise ProvisionError(
                f"source '{slug}' from config not found on the instance "
                f"(import incomplete?)"
            )
        source_id = source.get("id")
        key = dummy_apikey(slug) if using_dummy else apikey
        new_config = merge_header(source.get("configuration"), header, key)
        log(f"  source '{slug}' (id={source_id}): set {header}")
        client.put(f"{SOURCES_PATH}/{source_id}", {"configuration": new_config})

        after = client.get(f"{SOURCES_PATH}/{source_id}")
        got = (after.get("configuration") or {}).get(header) \
            if isinstance(after.get("configuration"), dict) else None
        if got != key:
            raise ProvisionError(
                f"source '{slug}': {header} did not reflect after PUT "
                f"(got {got!r})"
            )
        log(f"  source '{slug}': {header} reflected OK")
        done += 1
    return done


# --- CLI ---


def log(msg):
    print(f"==> {msg}", file=sys.stderr)


def _from_env(var, flag):
    """Read a non-empty value from env var `var`; raise if set-but-empty."""
    import os

    value = os.environ.get(var)
    if not value:
        raise ProvisionError(f"{flag} {var} is set but the env var is empty")
    return value


def resolve_apikey(args):
    """The real key from --apikey or --apikey-env, or None for dummy/test mode."""
    if args.apikey is not None:
        return args.apikey
    if args.apikey_env:
        return _from_env(args.apikey_env, "--apikey-env")
    return None


def resolve_password(args):
    """Password from --password or --password-env (kept out of argv)."""
    if args.password is not None:
        return args.password
    if args.password_env:
        return _from_env(args.password_env, "--password-env")
    raise ProvisionError("provide --password or --password-env")


def cmd_credentials(args):
    doc = load_config(args.config)
    apikey = resolve_apikey(args)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"provisioning credentials against {args.base}")
    count = provision_credentials(client, doc, apikey=apikey, header=args.header)
    log(f"CREDENTIALS PROVISIONED OK ({count} source(s))")
    return 0


def cmd_settings(args):
    client = Client(args.base, args.user, resolve_password(args))
    organisation = {"auto_create_default_organisation": True}
    if args.default_organisation:
        organisation["default_organisation"] = args.default_organisation
    multitenancy = {
        "enabled": args.multitenancy,
        "defaultUserTenant": "",
        "defaultObjectTenant": "",
        "adminOverride": True,
    }
    log(f"provisioning settings against {args.base}")
    provision_settings(client, organisation, multitenancy)
    log("SETTINGS PROVISIONED OK (organisation + multitenancy)")
    return 0


def cmd_verify_import(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"verifying imported config slugs against {args.base}")
    report = verify_import(client, doc)
    failed = False
    for bucket, info in report.items():
        missing = info["missing"]
        if missing:
            failed = True
            log(f"  {bucket}: MISSING {len(missing)}/{info['expected']}: {missing}")
        else:
            log(f"  {bucket}: {info['expected']}/{info['expected']} present OK")
    if failed:
        raise ProvisionError("config entities missing on the tenant — import incomplete")
    log("IMPORT VERIFIED OK (all config slugs present)")
    return 0


def cmd_sync_check(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"checking synchronization targets resolved on {args.base}")
    result = sync_check(client, doc)
    if result["dangling"]:
        for d in result["dangling"]:
            log(f"  DANGLING {d['slug']}: targetId={d['targetId']!r} (schema not resolved)")
        raise ProvisionError(
            f"{len(result['dangling'])} synchronization(s) have an unresolved target schema"
        )
    log(f"SYNC CHECK OK ({result['total']} syncs, all targets resolved)")
    return 0


def _add_connection_args(p, with_config=True):
    """Shared connection flags: base URL, user, and password (kept out of argv)."""
    p.add_argument("--base", required=True, help="instance base URL")
    p.add_argument("--user", required=True, help="admin user / app-password user")
    p.add_argument(
        "--password", default=None, help="password / app password (prefer --password-env)"
    )
    p.add_argument(
        "--password-env",
        default=None,
        help="read the password from this env var (kept out of argv)",
    )
    if with_config:
        p.add_argument(
            "--config",
            default="config/woo.configuration.json",
            help="config to drive the step (default: %(default)s)",
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description="Post-import tenant provisioning steps over the API."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    cred = sub.add_parser(
        "credentials",
        help="set every config source's API-key header and assert it reflects",
    )
    _add_connection_args(cred)
    cred.add_argument(
        "--apikey", default=None, help="real API key to set (omit to write a dummy test key)"
    )
    cred.add_argument(
        "--apikey-env", default=None, help="read the real API key from this env var (never logged)"
    )
    cred.add_argument(
        "--header", default=API_KEY_HEADER, help="source configuration key to set (default: %(default)s)"
    )
    cred.set_defaults(func=cmd_credentials)

    st = sub.add_parser(
        "settings",
        help="PUT organisation + multitenancy settings and assert they reflect",
    )
    _add_connection_args(st, with_config=False)
    st.add_argument(
        "--default-organisation",
        default=None,
        help="org UUID to set (omit to rely on auto_create_default_organisation)",
    )
    st.add_argument(
        "--multitenancy",
        action="store_true",
        help="enable multitenancy (default: disabled)",
    )
    st.set_defaults(func=cmd_settings)

    vi = sub.add_parser(
        "verify-import",
        help="assert every config slug (registers/schemas/sources/syncs) is present on the tenant",
    )
    _add_connection_args(vi)
    vi.set_defaults(func=cmd_verify_import)

    sc = sub.add_parser(
        "sync-check",
        help="assert every config synchronization resolved its target schema (no dangling slug)",
    )
    _add_connection_args(sc)
    sc.set_defaults(func=cmd_sync_check)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
