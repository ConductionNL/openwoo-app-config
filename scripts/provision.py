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

    def post_file(self, path, filename, content):
        """Multipart file upload (for the @NoCSRFRequired config import). Returns text."""
        boundary = "----provisionfileboundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.base}{path}", data=body, method="POST")
        req.add_header("Authorization", self.auth_header)
        req.add_header("OCS-APIREQUEST", "true")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise ProvisionError(f"POST {path} (file) -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProvisionError(f"POST {path} (file) -> {exc.reason}") from exc


class ProvisionError(Exception):
    """A provisioning step failed (HTTP error, refused auth, or failed assert)."""


# --- Commands ---

SOURCES_PATH = "/index.php/apps/openconnector/api/sources"
SYNCS_PATH = "/index.php/apps/openconnector/api/synchronizations"
JOBS_PATH = "/index.php/apps/openconnector/api/jobs"
REGISTERS_PATH = "/index.php/apps/openregister/api/registers"
SCHEMAS_PATH = "/index.php/apps/openregister/api/schemas"

# Config bucket -> the tenant list endpoint that should hold its rows after
# import. Used by verify_import to compare config slugs against what is actually
# present on the tenant (a slug-level check the bulk row count cannot give).
# Buckets the config leaves empty are skipped, so `jobs` only kicks in once the
# config carries jobs (expected after the OpenRegister import hotfix).
VERIFY_BUCKETS = {
    "registers": REGISTERS_PATH,
    "schemas": SCHEMAS_PATH,
    "sources": SOURCES_PATH,
    "synchronizations": SYNCS_PATH,
    "jobs": JOBS_PATH,
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


OBJECTS_PATH = "/index.php/apps/openregister/api/objects"


def provision_sync_run(client, doc, mode="run"):
    """POST run (or test) for every config synchronization, resolved by slug.

    `mode` is "run" (real execution) or "test" (dry-run). Returns a list of
    {slug, id}. Raises ProvisionError on a missing sync or a response that
    carries an error/exception (HTTP errors already raise in the client).
    NOTE: "run" against a real source performs a live data fetch — intended for
    a real tenant, not the local CI test.
    """
    want = config_slugs(doc, "synchronizations")
    syncs = results_list(client.get(SYNCS_PATH))
    done = []
    for slug in want:
        sync = find_by_slug(syncs, slug)
        if sync is None:
            raise ProvisionError(f"synchronization '{slug}' not found on the instance")
        sid = sync.get("id")
        resp = client.post(f"{SYNCS_PATH}/{sid}/{mode}")
        err = resp.get("error") or resp.get("exception") if isinstance(resp, dict) else None
        if err:
            raise ProvisionError(f"synchronization '{slug}' {mode} failed: {err}")
        log(f"  sync '{slug}' (id={sid}): {mode} OK")
        done.append({"slug": slug, "id": sid})
    return done


def provision_all(client, doc, apikey=None, settings=None, oc_settings=True,
                  do_import=True, catalog=True, run_syncs=False, sync_mode="test"):
    """Run the full post-install bring-up in order, asserting each step.

    Order mirrors a real tenant bring-up: settings -> OpenCatalogi register/schema
    coupling -> import the config -> verify it landed -> point the catalog at the
    WOO schemas -> source credentials -> synchronizations resolved -> optional
    per-sync run. Raises ProvisionError on the first failed assertion. The
    oc-settings / catalog steps need the OpenCatalogi base and the openregister
    schema API; skip them for a WOO-only tenant. Authorization flags
    (inheritFromPublic) import natively on OpenRegister 1.0.3+, so the
    `authorization` repair step is not part of the default flow. Object creation
    and job runs stay separate.
    """
    if settings is not None:
        log("[1/8] settings")
        provision_settings(client, settings["organisation"], settings["multitenancy"])
    else:
        log("[1/8] settings — skipped")

    if oc_settings:
        log("[2/8] oc-settings")
        provision_oc_settings(client)
    else:
        log("[2/8] oc-settings — skipped")

    if do_import:
        log("[3/8] import")
        provision_import(client, doc)
    else:
        log("[3/8] import — skipped")

    log("[4/8] verify-import")
    report = verify_import(client, doc)
    missing = {b: i["missing"] for b, i in report.items() if i["missing"]}
    if missing:
        raise ProvisionError(f"import incomplete, missing: {missing}")

    if catalog:
        log("[5/8] catalog")
        provision_catalog(client, doc)
    else:
        log("[5/8] catalog — skipped")

    log("[6/8] credentials")
    provision_credentials(client, doc, apikey=apikey)

    log("[7/8] sync-check")
    chk = sync_check(client, doc)
    if chk["dangling"]:
        raise ProvisionError(f"{len(chk['dangling'])} synchronization(s) dangling: {chk['dangling']}")

    if run_syncs:
        log(f"[8/8] sync-run ({sync_mode})")
        provision_sync_run(client, doc, mode=sync_mode)
    else:
        log("[8/8] sync-run — skipped (pass --run-syncs)")
    return True


IMPORT_PATH = "/index.php/apps/openregister/api/configurations/import"
# Authorization flag keys the `authorization` repair command enforces on the
# tenant. inheritFromPublic defaults to true; explicit false isolates
# publications per department (e.g. Almere). NOTE: OpenRegister 0.2.3's import
# rejected this key and silently dropped the schema; fixed in 1.0.3, which
# imports it natively (see docs/BUG-import-inheritFromPublic.md). `import` now
# uploads the config as-is; `authorization` remains for explicit repair (e.g.
# flipping inheritFromPublic to false on an existing tenant).
AUTH_FLAG_KEYS = {"inheritFromPublic"}


def provision_import(client, doc):
    """Upload the config as-is and assert the import reports success."""
    payload = json.dumps(doc).encode()
    log(f"  importing config ({len(bucket_items(doc, 'schemas'))} schemas)")
    raw = client.post_file(IMPORT_PATH, "woo.configuration.json", payload)
    if "Import successful" not in raw:
        raise ProvisionError(f"import did not report success: {raw[:200]}")
    log("  import OK (Import successful)")
    return True


def provision_authorization(client, doc):
    """Enforce the config's authorization flags (e.g. inheritFromPublic) on each
    schema via the schema UPDATE API and assert they reflect. A standalone repair
    step — the 1.0.3 import already carries these flags, but this can flip them on
    an existing tenant (e.g. set inheritFromPublic=false for department isolation).

    Only touches schemas whose config authorization carries a flag key, and merges
    into the schema's current authorization so the existing actions stay.
    """
    sch_ids = slug_to_id(results_list(client.get(SCHEMAS_PATH)))
    done = 0
    for schema in bucket_items(doc, "schemas"):
        slug = schema.get("slug")
        auth = schema.get("authorization")
        if not slug or not isinstance(auth, dict):
            continue
        flags = {k: auth[k] for k in AUTH_FLAG_KEYS if k in auth}
        if not flags:
            continue
        if slug not in sch_ids:
            raise ProvisionError(f"authorization: schema '{slug}' not on the tenant")
        sid = sch_ids[slug]
        current = client.get(f"{SCHEMAS_PATH}/{sid}").get("authorization") or {}
        merged = {**(current if isinstance(current, dict) else {}), **flags}
        client.put(f"{SCHEMAS_PATH}/{sid}", {"authorization": merged})
        after = client.get(f"{SCHEMAS_PATH}/{sid}").get("authorization") or {}
        bad = {k: v for k, v in flags.items() if after.get(k) != v}
        if bad:
            raise ProvisionError(f"authorization: schema '{slug}' did not reflect {bad}")
        log(f"  schema '{slug}': {flags} set OK")
        done += 1
    return done


CATALOG_REGISTER = "publication"
CATALOG_SCHEMA = "catalog"


def slug_to_id(items):
    """Map slug -> id for a list of entities (skips entries without a slug)."""
    return {i.get("slug"): i.get("id") for i in items if i.get("slug")}


def provision_catalog(client, doc, catalog_slug="publications", target_register="woo"):
    """Point the OpenCatalogi catalog object at the WOO register + all its schemas.

    The catalog lives in the OpenCatalogi base (register `publication`, schema
    `catalog`). We resolve the WOO register slug and every config schema slug to
    their tenant ids (the object stores numeric ids), set them on the catalog,
    PUT, then assert the registers/schemas reflect. Raises if the base catalog
    object or any config schema is absent on the tenant.
    """
    reg_ids = slug_to_id(results_list(client.get(REGISTERS_PATH)))
    sch_ids = slug_to_id(results_list(client.get(SCHEMAS_PATH)))
    if target_register not in reg_ids:
        raise ProvisionError(f"catalog: register '{target_register}' not found on the tenant")
    want = config_slugs(doc, "schemas")
    missing = [s for s in want if s not in sch_ids]
    if missing:
        raise ProvisionError(f"catalog: {len(missing)} schema(s) not on the tenant: {missing}")
    register_ids = [reg_ids[target_register]]
    schema_ids = [sch_ids[s] for s in want]

    obj_path = f"{OBJECTS_PATH}/{CATALOG_REGISTER}/{CATALOG_SCHEMA}/{catalog_slug}"
    existing = client.get(obj_path)
    body = dict(existing) if isinstance(existing, dict) else {}
    body["registers"] = register_ids
    body["schemas"] = schema_ids
    log(f"  catalog '{catalog_slug}': register {register_ids} + {len(schema_ids)} schemas")
    client.put(obj_path, body)

    after = client.get(obj_path)
    got_reg = after.get("registers") or []
    got_sch = sorted(after.get("schemas") or [])
    if got_reg != register_ids or got_sch != sorted(schema_ids):
        raise ProvisionError(
            f"catalog did not reflect (registers={got_reg}, {len(got_sch)} schemas)"
        )
    log(f"  catalog '{catalog_slug}': {len(schema_ids)} schemas reflected OK")
    return after


def provision_object(client, register, schema, payload):
    """POST one object into register/schema and assert the response carries an id.

    register/schema may be slugs or numeric ids. Returns the created object.
    """
    resp = client.post(f"{OBJECTS_PATH}/{register}/{schema}", payload)
    oid = (resp.get("id") or resp.get("uuid")) if isinstance(resp, dict) else None
    if not oid:
        raise ProvisionError(
            f"object create in {register}/{schema} returned no id/uuid: {str(resp)[:200]}"
        )
    return resp


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


OC_SETTINGS_PATH = "/index.php/apps/opencatalogi/api/settings"
# OpenCatalogi object types; each is backed by a same-named schema in the
# OpenCatalogi base register (`publication`).
OC_OBJECT_TYPES = ["catalog", "listing", "organization", "theme", "page", "menu", "glossary"]


def provision_oc_settings(client, register="publication", object_types=None):
    """Couple each OpenCatalogi object type to its register + schema.

    POSTs `{type}_source/_register/_schema` for catalog/listing/organization/
    theme/page/menu/glossary, resolving the register slug and each type's
    same-named schema slug to tenant ids, then GETs the settings back and
    asserts the coupling reflects. Requires the OpenCatalogi base schemas.
    """
    types = object_types or OC_OBJECT_TYPES
    reg_ids = slug_to_id(results_list(client.get(REGISTERS_PATH)))
    sch_ids = slug_to_id(results_list(client.get(SCHEMAS_PATH)))
    if register not in reg_ids:
        raise ProvisionError(f"oc-settings: register '{register}' not found on the tenant")
    missing = [t for t in types if t not in sch_ids]
    if missing:
        raise ProvisionError(f"oc-settings: schema(s) not on the tenant: {missing}")
    payload = {}
    for t in types:
        payload[f"{t}_source"] = "openregister"
        payload[f"{t}_register"] = str(reg_ids[register])
        payload[f"{t}_schema"] = str(sch_ids[t])
    log(f"  coupling {len(types)} object types to register '{register}'")
    client.post(OC_SETTINGS_PATH, payload)

    after = client.get(OC_SETTINGS_PATH)
    conf = after.get("configuration", after) if isinstance(after, dict) else {}
    bad = [k for k, v in payload.items() if str(conf.get(k)) != str(v)]
    if bad:
        raise ProvisionError(f"oc-settings did not reflect: {bad}")
    log(f"  oc-settings: {len(types)} object types coupled OK")
    return conf


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
    """Password from --password, --password-env, or an interactive prompt.

    When neither flag is given and we have a terminal, prompt with getpass so the
    secret never lands in argv, shell history or a file.
    """
    if args.password is not None:
        return args.password
    if args.password_env:
        return _from_env(args.password_env, "--password-env")
    import getpass
    import sys as _sys

    if _sys.stdin.isatty():
        value = getpass.getpass(f"App password for {args.user} @ {args.base}: ")
        if value:
            return value
    raise ProvisionError("provide --password, --password-env, or run interactively")


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


def _settings_from_args(args):
    """Build the settings payloads from CLI args, or None when --skip-settings."""
    if args.skip_settings:
        return None
    organisation = {"auto_create_default_organisation": True}
    if args.default_organisation:
        organisation["default_organisation"] = args.default_organisation
    multitenancy = {
        "enabled": args.multitenancy,
        "defaultUserTenant": "",
        "defaultObjectTenant": "",
        "adminOverride": True,
    }
    return {"organisation": organisation, "multitenancy": multitenancy}


def cmd_all(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"full provisioning against {args.base}")
    provision_all(
        client,
        doc,
        apikey=resolve_apikey(args),
        settings=_settings_from_args(args),
        oc_settings=not args.skip_oc_settings,
        do_import=not args.skip_import,
        catalog=not args.skip_catalog,
        run_syncs=args.run_syncs,
        sync_mode="test" if args.test else "run",
    )
    log("FULL PROVISIONING OK")
    return 0


def cmd_sync_run(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    mode = "test" if args.test else "run"
    log(f"{mode}ning {len(config_slugs(doc, 'synchronizations'))} synchronization(s) on {args.base}")
    done = provision_sync_run(client, doc, mode=mode)
    log(f"SYNC {mode.upper()} OK ({len(done)} synchronization(s))")
    return 0


def cmd_catalog(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"provisioning catalog '{args.catalog_slug}' against {args.base}")
    provision_catalog(client, doc, catalog_slug=args.catalog_slug, target_register=args.register)
    log("CATALOG PROVISIONED OK")
    return 0


def cmd_objects(args):
    client = Client(args.base, args.user, resolve_password(args))
    with open(args.payload_file, encoding="utf-8") as fh:
        payload = json.load(fh)
    log(f"creating object in {args.register}/{args.schema} on {args.base}")
    obj = provision_object(client, args.register, args.schema, payload)
    log(f"OBJECT CREATED OK (id={obj.get('id') or obj.get('uuid')})")
    return 0


def cmd_import(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"importing config into {args.base}")
    provision_import(client, doc)
    log("IMPORT OK")
    return 0


def cmd_authorization(args):
    doc = load_config(args.config)
    client = Client(args.base, args.user, resolve_password(args))
    log(f"restoring schema authorization on {args.base}")
    n = provision_authorization(client, doc)
    log(f"AUTHORIZATION OK ({n} schema(s) patched)")
    return 0


def cmd_oc_settings(args):
    client = Client(args.base, args.user, resolve_password(args))
    log(f"provisioning OpenCatalogi settings against {args.base}")
    provision_oc_settings(client, register=args.register)
    log("OC-SETTINGS PROVISIONED OK")
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

    im = sub.add_parser(
        "import",
        help="upload the config (stripping import-hostile authorization keys) and assert success",
    )
    _add_connection_args(im)
    im.set_defaults(func=cmd_import)

    au = sub.add_parser(
        "authorization",
        help="restore import-stripped authorization keys (e.g. inheritFromPublic) via the schema API",
    )
    _add_connection_args(au)
    au.set_defaults(func=cmd_authorization)

    ocs = sub.add_parser(
        "oc-settings",
        help="couple OpenCatalogi object types (catalog/listing/…) to their register + schema",
    )
    _add_connection_args(ocs, with_config=False)
    ocs.add_argument("--register", default="publication", help="OpenCatalogi base register slug (default: %(default)s)")
    ocs.set_defaults(func=cmd_oc_settings)

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

    sr = sub.add_parser(
        "sync-run",
        help="POST run (or --test) for every config synchronization; real run fetches live data",
    )
    _add_connection_args(sr)
    sr.add_argument(
        "--test",
        action="store_true",
        help="use the /test dry-run endpoint instead of /run (no real fetch)",
    )
    sr.set_defaults(func=cmd_sync_run)

    ob = sub.add_parser(
        "objects",
        help="create one object in a register/schema from a JSON payload file",
    )
    _add_connection_args(ob, with_config=False)
    ob.add_argument("--register", required=True, help="target register (slug or id)")
    ob.add_argument("--schema", required=True, help="target schema (slug or id)")
    ob.add_argument("--payload-file", required=True, help="JSON file with the object body")
    ob.set_defaults(func=cmd_objects)

    cat = sub.add_parser(
        "catalog",
        help="point the OpenCatalogi catalog object at the WOO register + all its schemas",
    )
    _add_connection_args(cat)
    cat.add_argument("--catalog-slug", default="publications", help="catalog object slug (default: %(default)s)")
    cat.add_argument("--register", default="woo", help="register slug to link (default: %(default)s)")
    cat.set_defaults(func=cmd_catalog)

    al = sub.add_parser(
        "all",
        help="run the full bring-up in order: settings -> verify-import -> credentials -> sync-check [-> sync-run]",
    )
    _add_connection_args(al)
    al.add_argument("--apikey", default=None, help="real API key for the source (omit for dummy)")
    al.add_argument("--apikey-env", default=None, help="read the real API key from this env var")
    al.add_argument("--default-organisation", default=None, help="org UUID (omit to rely on auto-create)")
    al.add_argument("--multitenancy", action="store_true", help="enable multitenancy (default: disabled)")
    al.add_argument("--skip-settings", action="store_true", help="do not touch settings")
    al.add_argument("--skip-import", action="store_true", help="skip the config import (assume already imported)")
    al.add_argument("--skip-oc-settings", action="store_true", help="skip the OpenCatalogi register/schema coupling")
    al.add_argument("--skip-catalog", action="store_true", help="skip pointing the catalog at the WOO schemas")
    al.add_argument("--run-syncs", action="store_true", help="also run each synchronization at the end")
    al.add_argument("--test", action="store_true", help="with --run-syncs, use the /test dry-run endpoint")
    al.set_defaults(func=cmd_all)

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
