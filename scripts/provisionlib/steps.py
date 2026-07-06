# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/steps.py — the provisioning steps (domain logic).
#
# Each provision_* function drives one part of a tenant into the state the WOO
# config describes and asserts it took effect ("test what you ship"). All take a
# Client and are idempotent upserts. provision_all() runs them in order, gating
# each step. Raises ProvisionError on the first failed assertion.
"""Provisioning + validation steps over the OpenRegister/OpenConnector/OpenCatalogi API."""

import json

from .client import ProvisionError
from .constants import *  # noqa: F401,F403 — API paths + defaults (curated literals)
from .helpers import (
    bucket_items,
    config_slugs,
    config_source_slugs,
    dummy_apikey,
    find_by_slug,
    log,
    merge_header,
    results_list,
    slug_to_id,
)

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


def _is_numeric(value):
    return isinstance(value, int) or (isinstance(value, str) and value.isdigit())


def provision_jobs(client, doc, job_user=None):
    """Resolve each job's synchronizationId (sync slug -> tenant numeric id) and,
    optionally, set the job's `userId`.

    The config (portable) carries the sync *slug* in `arguments.synchronizationId`;
    the import leaves it a slug, but the SynchronizationAction needs the numeric
    sync id to trigger. `job_user`, when given, sets each job's `userId` (the CLI
    defaults it to the admin --user) — a workaround for scheduled jobs running as
    Anonymous and being denied object writes (see
    docs/notes/BUG-sync-job-anonymous-permission.md); only effective if the runner
    honours userId. Driven by config jobs, matched to tenant jobs by slug. Skips a
    job only when there is nothing to change. Raises on a missing job or an
    unresolvable sync slug.
    """
    sync_ids = slug_to_id(results_list(client.get(SYNCS_PATH)))
    tenant_jobs = {j.get("slug"): j for j in results_list(client.get(JOBS_PATH)) if j.get("slug")}
    done = 0
    for job in bucket_items(doc, "jobs"):
        slug = job.get("slug")
        if not slug:
            continue
        if slug not in tenant_jobs:
            raise ProvisionError(f"jobs: job '{slug}' not on the tenant")
        tj = tenant_jobs[slug]
        body = {}

        ref = (job.get("arguments") or {}).get("synchronizationId")
        if ref is not None:
            if _is_numeric(ref):
                desired = ref
            elif ref in sync_ids:
                desired = sync_ids[ref]
            else:
                raise ProvisionError(f"jobs: job '{slug}' synchronizationId '{ref}' is not a tenant sync slug")
            current = (tj.get("arguments") or {}).get("synchronizationId")
            if str(current) != str(desired):   # tenant not yet resolved to the numeric id
                body["arguments"] = {**(tj.get("arguments") or {}), "synchronizationId": desired}
        if job_user is not None and tj.get("userId") != job_user:
            body["userId"] = job_user
        if not body:
            continue  # tenant job already has the right synchronizationId and userId

        resp = client.put(f"{JOBS_PATH}/{tj['id']}", body)
        resp = resp if isinstance(resp, dict) else {}
        if "arguments" in body:
            want = body["arguments"]["synchronizationId"]
            if (resp.get("arguments") or {}).get("synchronizationId") != want:
                raise ProvisionError(f"jobs: job '{slug}' synchronizationId did not reflect")
        if job_user is not None and resp.get("userId") != job_user:
            raise ProvisionError(f"jobs: job '{slug}' userId did not reflect (got {resp.get('userId')!r})")
        changed = []
        if "arguments" in body:
            changed.append(f"synchronizationId={body['arguments']['synchronizationId']}")
        if job_user is not None:
            changed.append(f"userId={job_user}")
        log(f"  job '{slug}': {', '.join(changed)} OK")
        done += 1
    return done


def _resolve_target_id(target, registers, schemas):
    """Resolve a 'registerslug/schemaslug' targetId to 'regid/schemaid'. Returns
    None if either side does not resolve. Already-numeric sides pass through."""
    if not isinstance(target, str) or "/" not in target:
        return None
    reg, sch = target.split("/", 1)
    rid = reg if _is_numeric(reg) else registers.get(reg)
    sid = sch if _is_numeric(sch) else schemas.get(sch)
    if rid is None or sid is None:
        return None
    return f"{rid}/{sid}"


def provision_syncs(client, doc):
    """Resolve each synchronization's slug references to tenant numeric ids.

    The config is portable and carries *slugs* for `sourceId`, `sourceTargetMapping`,
    `actions` (rule slugs) and `targetId` ('register/schema'). The import only
    resolves cross-object references against what already exists in the SAME pass,
    so on a fresh tenant these forward references (sync -> source/mapping/rule/
    register/schema) are left as slugs and break at run time — e.g.
    `SQLSTATE[22P02] invalid input syntax for type bigint: "demo-xxllnc"` when the
    sync's sourceId is still the source slug. (Re-importing only shifts the problem:
    the second pass resolves the syncs but re-slugs the jobs.) So we resolve them
    here, post-import, against the final numeric ids — the same approach
    provision_jobs uses for a job's synchronizationId. Idempotent: a field is only
    PUT when it differs; a fully-resolved tenant is a no-op. Matched by slug.
    """
    sources = slug_to_id(results_list(client.get(SOURCES_PATH)))
    mappings = slug_to_id(results_list(client.get(MAPPINGS_PATH)))
    rules = slug_to_id(results_list(client.get(RULES_PATH)))
    registers = slug_to_id(results_list(client.get(REGISTERS_PATH)))
    schemas = slug_to_id(results_list(client.get(SCHEMAS_PATH)))
    tenant = {s.get("slug"): s for s in results_list(client.get(SYNCS_PATH)) if s.get("slug")}
    done = 0
    for sync in bucket_items(doc, "synchronizations"):
        slug = sync.get("slug")
        if not slug:
            continue
        if slug not in tenant:
            raise ProvisionError(f"syncs: synchronization '{slug}' not on the tenant")
        ts = tenant[slug]
        body = {}

        src = sync.get("sourceId")
        if src is not None and not _is_numeric(src):
            if src not in sources:
                raise ProvisionError(f"syncs: '{slug}' sourceId '{src}' is not a tenant source slug")
            if str(ts.get("sourceId")) != str(sources[src]):
                body["sourceId"] = sources[src]

        mp = sync.get("sourceTargetMapping")
        if mp and not _is_numeric(mp):
            if mp not in mappings:
                raise ProvisionError(f"syncs: '{slug}' sourceTargetMapping '{mp}' is not a tenant mapping slug")
            if str(ts.get("sourceTargetMapping")) != str(mappings[mp]):
                body["sourceTargetMapping"] = mappings[mp]

        acts = sync.get("actions")
        if isinstance(acts, list) and acts:
            resolved = []
            for a in acts:
                if _is_numeric(a):
                    resolved.append(int(a) if isinstance(a, str) else a)
                elif a in rules:
                    resolved.append(rules[a])
                else:
                    raise ProvisionError(f"syncs: '{slug}' action '{a}' is not a tenant rule slug")
            if [str(x) for x in (ts.get("actions") or [])] != [str(x) for x in resolved]:
                body["actions"] = resolved

        tgt = sync.get("targetId")
        if isinstance(tgt, str) and "/" in tgt and not all(p.isdigit() for p in tgt.split("/")):
            desired = _resolve_target_id(tgt, registers, schemas)
            if desired is None:
                raise ProvisionError(f"syncs: '{slug}' targetId '{tgt}' did not resolve to register/schema ids")
            if str(ts.get("targetId")) != str(desired):
                body["targetId"] = desired

        if not body:
            continue
        client.put(f"{SYNCS_PATH}/{ts['id']}", body)
        after = client.get(f"{SYNCS_PATH}/{ts['id']}")
        after = after if isinstance(after, dict) else {}
        for k, v in body.items():
            got = after.get(k)
            if k == "actions":
                if [str(x) for x in (got or [])] != [str(x) for x in v]:
                    raise ProvisionError(f"syncs: '{slug}' actions did not reflect (got {got!r})")
            elif str(got) != str(v):
                raise ProvisionError(f"syncs: '{slug}' {k} did not reflect (got {got!r}, want {v!r})")
        log(f"  sync '{slug}': {', '.join(f'{k}={v}' for k, v in body.items())} OK")
        done += 1
    return done


def provision_rules(client, doc):
    """Resolve each `fetch_file` rule's source slug -> tenant numeric source id.

    Same forward-reference problem as provision_syncs, for the rule object: the
    config carries `configuration.fetch_file.source` as the source *slug*
    (`demo-xxllnc`), and the import leaves it a slug (or drops it) on a fresh
    tenant, so the fetch_file action cannot resolve the source. Resolve it
    post-import to the numeric id. Idempotent; matched by slug; PUTs the full rule
    back (the API merges/echoes the object). Only touches fetch_file rules.
    """
    sources = slug_to_id(results_list(client.get(SOURCES_PATH)))
    tenant = {r.get("slug"): r for r in results_list(client.get(RULES_PATH)) if r.get("slug")}
    done = 0
    for rule in bucket_items(doc, "rules"):
        slug = rule.get("slug")
        cfg = (rule.get("configuration") or {}).get("fetch_file")
        if not slug or not isinstance(cfg, dict) or "source" not in cfg:
            continue
        if slug not in tenant:
            raise ProvisionError(f"rules: rule '{slug}' not on the tenant")
        want = cfg["source"]
        if not _is_numeric(want):
            if want not in sources:
                raise ProvisionError(f"rules: '{slug}' source '{want}' is not a tenant source slug")
            want = sources[want]
        tr = tenant[slug]
        tr_ff = (tr.get("configuration") or {}).get("fetch_file") or {}
        if str(tr_ff.get("source")) == str(want):
            continue  # already resolved
        body = dict(tr)
        body["configuration"] = {**(tr.get("configuration") or {}),
                                 "fetch_file": {**tr_ff, "source": want}}
        client.put(f"{RULES_PATH}/{tr['id']}", body)
        after = (client.get(f"{RULES_PATH}/{tr['id']}").get("configuration") or {}).get("fetch_file") or {}
        if str(after.get("source")) != str(want):
            raise ProvisionError(f"rules: '{slug}' source did not reflect (got {after.get('source')!r})")
        log(f"  rule '{slug}': fetch_file.source={want} OK")
        done += 1
    return done


def provision_all(client, doc, apikey=None, source_url=None, interface_id=None,
                  settings=None, oc_settings=True, do_import=True, force_import=False,
                  catalog=True, delete_menu=True, menu_name=USER_MENU_NAME,
                  do_credentials=True, job_user=None, run_syncs=False,
                  sync_mode="test"):
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
        log("[1/11] settings")
        provision_settings(client, settings["organisation"], settings["multitenancy"])
    else:
        log("[1/11] settings — skipped")

    if oc_settings:
        log("[2/11] oc-settings")
        provision_oc_settings(client)
    else:
        log("[2/11] oc-settings — skipped")

    if do_import:
        log("[3/11] import")
        provision_import(client, doc, force=force_import)
    else:
        log("[3/11] import — skipped")

    log("[4/11] verify-import")
    report = verify_import(client, doc)
    missing = {b: i["missing"] for b, i in report.items() if i["missing"]}
    if missing:
        raise ProvisionError(f"import incomplete, missing: {missing}")

    if catalog:
        log("[5/11] catalog")
        provision_catalog(client, doc)
    else:
        log("[5/11] catalog — skipped")

    if delete_menu:
        log("[6/11] delete-menu")
        provision_delete_menu(client, name=menu_name)
    else:
        log("[6/11] delete-menu — skipped")

    if do_credentials:
        log("[7/11] credentials")
        provision_credentials(client, doc, apikey=apikey, source_url=source_url,
                              interface_id=interface_id)
    else:
        log("[7/11] credentials — skipped (per-tenant source params set out-of-band)")

    # Resolve the synchronizations' own slug references (sourceId / mapping / rules
    # / targetId) before the check + run — the import leaves forward references as
    # slugs on a fresh tenant (see provision_syncs).
    log("[8/11] sync-refs")
    n = provision_syncs(client, doc)
    log(f"  sync-refs: {n} synchronization(s) resolved")
    nr = provision_rules(client, doc)
    log(f"  sync-refs: {nr} rule(s) resolved")

    log("[9/11] sync-check")
    chk = sync_check(client, doc)
    if chk["dangling"]:
        raise ProvisionError(f"{len(chk['dangling'])} synchronization(s) dangling: {chk['dangling']}")

    log("[10/11] jobs")
    n = provision_jobs(client, doc, job_user=job_user)
    log(f"  jobs: {n} job(s) updated"
        f"{f' (userId={job_user})' if job_user else ''}")

    if run_syncs:
        log(f"[11/11] sync-run ({sync_mode})")
        provision_sync_run(client, doc, mode=sync_mode)
    else:
        log("[11/11] sync-run — skipped (pass --run-syncs)")
    return True


def provision_import(client, doc, force=False):
    """Upload the config and assert the import reports success.

    Idempotent: unless `force`, first checks whether every config slug is already
    present (verify_import) and skips the upload if so. Use force=True to re-upload
    when the config *content* changed (a slug-level check can't see that).
    """
    if not force:
        missing = {b: i["missing"] for b, i in verify_import(client, doc).items() if i["missing"]}
        if not missing:
            log("  config already present — skipping upload (use --force-import to re-upload)")
            return True
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
    if isinstance(existing, dict) and (existing.get("registers") or []) == register_ids \
            and sorted(existing.get("schemas") or []) == sorted(schema_ids):
        log(f"  catalog '{catalog_slug}': already points at {len(schema_ids)} schemas, skipping")
        return existing
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


def _menu_matches(obj, name):
    """True if a menu object's name/title/slug equals `name` (case-insensitive)."""
    target = name.strip().lower()
    for key in ("name", "title", "slug"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip().lower() == target:
            return True
    return False


def provision_delete_menu(client, name=USER_MENU_NAME):
    """Delete the OpenCatalogi default '{name}' menu object if present.

    GETs the publication/menu objects, matches `name` against each object's
    name/title/slug (case-insensitive), DELETEs every match by uuid (falling back
    to id), then re-GETs the list to assert each is gone. Idempotent: returns 0
    and skips when no match is found. Returns the number of objects deleted.
    """
    list_path = f"{OBJECTS_PATH}/{MENU_REGISTER}/{MENU_SCHEMA}"
    objects = results_list(client.get(list_path))
    matches = [o for o in objects if _menu_matches(o, name)]
    if not matches:
        log(f"  no '{name}' menu object found — skipping")
        return 0
    deleted = []
    for obj in matches:
        ident = obj.get("uuid") or obj.get("id")
        if not ident:
            raise ProvisionError(f"menu object matched '{name}' but has no uuid/id: {str(obj)[:120]}")
        log(f"  deleting '{name}' menu object (id={ident})")
        client.delete(f"{list_path}/{ident}")
        deleted.append(str(ident))

    remaining = results_list(client.get(list_path))
    still = [str(o.get("uuid") or o.get("id")) for o in remaining if _menu_matches(o, name)]
    leftover = [d for d in deleted if d in still]
    if leftover:
        raise ProvisionError(f"'{name}' menu object(s) still present after delete: {leftover}")
    log(f"  deleted {len(deleted)} '{name}' menu object(s) OK")
    return len(deleted)


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


def put_settings_reflected(client, path, key, payload, wrapped=True):
    """PUT a settings payload, GET it back, assert it reflects.

    `organisation`/`multitenancy` return the saved values wrapped under a single
    key (`{"organisation": {...}}`); `retention` returns them unwrapped (the dict
    itself). `wrapped` selects which. We only assert the fields we sent, so extra
    server-side fields (e.g. an auto-created org id) are fine.
    """
    def unwrap(resp):
        if not isinstance(resp, dict):
            return {}
        return resp.get(key, {}) if wrapped else resp
    current = unwrap(client.get(path))
    if isinstance(current, dict) and all(current.get(k) == v for k, v in payload.items()):
        log(f"  {key}: already set, skipping")
        return current
    client.put(path, payload)
    got = unwrap(client.get(path))
    mismatched = {k: got.get(k) for k, v in payload.items() if got.get(k) != v}
    if mismatched:
        raise ProvisionError(
            f"{path}: settings did not reflect (sent {payload}, got back {mismatched})"
        )
    return got


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

    def coupling(resp):
        return resp.get("configuration", resp) if isinstance(resp, dict) else {}

    current = coupling(client.get(OC_SETTINGS_PATH))
    if all(str(current.get(k)) == str(v) for k, v in payload.items()):
        log(f"  oc-settings: {len(types)} object types already coupled, skipping")
        return current
    log(f"  coupling {len(types)} object types to register '{register}'")
    client.post(OC_SETTINGS_PATH, payload)

    conf = coupling(client.get(OC_SETTINGS_PATH))
    bad = [k for k, v in payload.items() if str(conf.get(k)) != str(v)]
    if bad:
        raise ProvisionError(f"oc-settings did not reflect: {bad}")
    log(f"  oc-settings: {len(types)} object types coupled OK")
    return conf


def provision_settings(client, organisation, multitenancy):
    """PUT the organisation, multitenancy and retention settings and assert each
    reflects. Retention disables audit + search trails (see RETENTION_SETTINGS)."""
    log("  PUT settings/organisation")
    org = put_settings_reflected(client, SETTINGS_ORG_PATH, "organisation", organisation)
    log("  PUT settings/multitenancy")
    mt = put_settings_reflected(client, SETTINGS_MT_PATH, "multitenancy", multitenancy)
    log("  PUT settings/retention (audit/search trails off)")
    ret = put_settings_reflected(client, SETTINGS_RETENTION_PATH, "retention",
                                 RETENTION_SETTINGS, wrapped=False)
    files = None
    try:
        log("  PUT settings/files (text extraction = manual)")
        files = put_settings_reflected(client, SETTINGS_FILES_PATH, "files",
                                       FILE_SETTINGS, wrapped=False)
    except ProvisionError as exc:
        # /settings/files is 1.1.x+; skip gracefully on older tenants.
        log(f"  settings/files unavailable (pre-1.1.x?), skipping text-extraction: {str(exc)[:80]}")
    return {"organisation": org, "multitenancy": mt, "retention": ret, "files": files}


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


def provision_credentials(client, doc, apikey=None, source_url=None, interface_id=None,
                          header=API_KEY_HEADER):
    """Set every config source's connection params on the tenant and assert they stick.

    Per-tenant params (none committed to the config): `apikey` (the API-KEY
    header), `source_url` (the source `location`), and `interface_id` (the
    API-Interface-ID header). When `apikey` is None a per-slug dummy is used so
    the path is still exercised in CI; `source_url`/`interface_id` left None keep
    whatever the config imported. Headers are merged into the source
    `configuration` (read-modify-write) so other headers are preserved.

    Returns the number of sources provisioned. Raises ProvisionError on the first
    hard failure (missing source, refused auth, a value did not reflect).
    """
    slugs = config_source_slugs(doc)
    if not slugs:
        log("no sources in config — nothing to provision")
        return 0

    using_dummy = apikey is None
    log(f"key source: {'dummy (test mode)' if using_dummy else 'supplied key'}"
        f"{'; setting source URL' if source_url else ''}"
        f"{'; setting API-Interface-ID' if interface_id else ''}")
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
        cur_conf = source.get("configuration") if isinstance(source.get("configuration"), dict) else {}
        already = (cur_conf.get(header) == key
                   and (interface_id is None or cur_conf.get(INTERFACE_ID_HEADER) == interface_id)
                   and (not source_url or source.get("location") == source_url))
        if already:
            log(f"  source '{slug}' (id={source_id}): connection params already set, skipping")
            done += 1
            continue
        new_config = merge_header(source.get("configuration"), header, key)
        if interface_id is not None:
            new_config[INTERFACE_ID_HEADER] = interface_id
        body = {"configuration": new_config}
        if source_url:
            body["location"] = source_url
        log(f"  source '{slug}' (id={source_id}): set {header}"
            f"{' + location' if source_url else ''}"
            f"{' + API-Interface-ID' if interface_id is not None else ''}")
        client.put(f"{SOURCES_PATH}/{source_id}", body)

        after = client.get(f"{SOURCES_PATH}/{source_id}")
        conf = after.get("configuration") if isinstance(after.get("configuration"), dict) else {}
        problems = []
        if conf.get(header) != key:
            problems.append(f"{header}={conf.get(header)!r}")
        if interface_id is not None and conf.get(INTERFACE_ID_HEADER) != interface_id:
            problems.append(f"{INTERFACE_ID_HEADER}={conf.get(INTERFACE_ID_HEADER)!r}")
        if source_url and after.get("location") != source_url:
            problems.append(f"location={after.get('location')!r}")
        if problems:
            raise ProvisionError(f"source '{slug}': did not reflect after PUT ({', '.join(problems)})")
        log(f"  source '{slug}': connection params reflected OK")
        done += 1
    return done
