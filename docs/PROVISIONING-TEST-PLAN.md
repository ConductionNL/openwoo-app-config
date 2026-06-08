# Provisioning test plan (full-flow Layer 2)

Status: **planned** — current `make functional` does install → import → verify
row counts. This document is the agreed plan to grow it into a full
provisioning test that mirrors a real tenant bring-up. Pick up here.

## Goal

Test the whole tenant flow end-to-end in the ephemeral stack:

```
install → import config → settings → credentials → default-catalog/home → asserts
```

## Decisions (2026-06-08)

- **Provisioner lives in this repo** — a clean, zero-dependency stdlib Python
  provisioner driven by our sanitized `config/woo.configuration.json`. The
  functional test calls it. Later, `CONDUCTION/toolchain/scripts/configure_apps.py`
  should be consolidated onto this (one source of truth). "Test what you ship."
- **Credentials test = step + dummy-cred assertion** — PATCH a dummy apikey onto
  the source and assert the source reflects it. No real data fetch (the demo
  source `demo-xxllnc` is `auth: none`; no real credentialed source available).

## Endpoints (from configure_apps.py, all under `{base}/index.php`)

| Step | Method + path | Auth |
|------|---------------|------|
| Settings | `POST /apps/opencatalogi/api/settings` | CSRF requesttoken |
| Import | `POST /apps/openregister/api/configurations/import` | basic-auth (`@NoCSRFRequired`) |
| Credentials | `PATCH /apps/openconnector/api/sources/{id}` | CSRF requesttoken |
| Default catalog | `PATCH /apps/openregister/api/objects/publication/catalog/default-catalog` | CSRF requesttoken |
| Home page | `POST /apps/openregister/api/objects/1/5` | CSRF requesttoken |

### CSRF flow (needed for everything except import/export)

`GET {base}/` → scrape `data-requesttoken="..."` from the HTML → send it as the
`requesttoken` header (plus `OCS-APIREQUEST: true`) on subsequent state-changing
calls, reusing the session cookie. Basic-auth alone only works for the
`@NoCSRFRequired` import/export routes. (Pattern proven in configure_apps.py
Step 0 / `build_headers`.)

## Gotchas to fix in our version

1. configure_apps.py runs **settings before import**, but the settings payload
   references schema IDs `2..8` that only exist after import. It only "works"
   because settings store the IDs blindly. **We import first.**
2. catalog/home steps use **hardcoded IDs** (`schemas/2`, `objects/1/5`) that
   assume a deterministic import order. Either keep the assumption (document it)
   or resolve IDs by slug after import (more robust).

## Settings payload (OpenCatalogi)

`{catalog,listing,organization,theme,page,menu,glossary}_source = "openregister"`
plus `_register`/`_schema` numeric IDs (see configure_apps.py lines ~117-140).
Prefer resolving these IDs from the imported registers/schemas by slug rather
than hardcoding.

## Assertions to add (on top of existing row-count check)

- settings: GET settings echoes the values we POSTed.
- credentials: source `{id}` reflects the dummy apikey after PATCH.
- catalog: the `default-catalog` publication object exists.
- (optional) trigger one synchronization and assert it runs without error
  (no data fetch expected against the auth:none demo source).

## Where to plug it in

Extend `scripts/functional-test.sh` to call a new `scripts/provision.py`
(stdlib, urllib) after `import_config`, then assert. Keep the ephemeral
docker-compose stack and teardown as-is. Local-only (Codeberg = buildah).

## Current repo state

- `make lint` / `make test` / `make functional` all green.
- `make functional` proven end-to-end on NC30 + openregister 1.0.3 /
  openconnector 0.2.20 / opencatalogi 1.0.3 (17 schemas, 16 syncs imported).
- 1 commit may be unpushed (`Harden functional test...`) — check `git status`.
- Source credentials for real (credentialed) sources must come from a
  K8s secret / ESO, never the config JSON — separate prod provisioning step.
