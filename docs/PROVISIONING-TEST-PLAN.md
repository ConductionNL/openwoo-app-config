# Provisioning test plan (full-flow Layer 2)

Status: **in progress.** `make functional` now does install → import → verify
row counts → **provision source credentials** (set the API-key header, assert it
reflects). The OpenCatalogi settings / default-catalog / home-page steps are
**out of scope for this config** — see "Finding (2026-06-08)" below.

## Done (2026-06-08)

- **Credentials step shipped.** `scripts/provision.py credentials` resolves every
  config source by slug on the running instance, sets `configuration."headers.API-KEY"`
  (read-modify-write, preserving `API-Interface-ID` etc.), then GETs it back and
  asserts the key reflected. Wired into `scripts/functional-test.sh` after the
  row-count check. Proven end-to-end on NC30. The config ships an **empty**
  `headers.API-KEY` placeholder; the real key is injected at provision time from
  `--apikey` / `--apikey-env` (never committed), dummy in CI/local test.

## Finding (2026-06-08): settings/catalog/home are NOT WOO-config steps

A fresh import of **this** config yields register `woo` + 17 WOO schemas and a
single source — and **no** `publication` register, no `catalog`/`listing`/
`organization`/`theme`/`page`/`menu`/`glossary` schemas. Those entities (and the
schema IDs `2..8` the original plan referenced) belong to **OpenCatalogi's own
base configuration**, not the WOO config. Verified empirically:
`PATCH /api/objects/publication/catalog/default-catalog` → 404 "Register not
found: 'publication'". So:

- The settings / default-catalog / home-page steps require importing the
  OpenCatalogi base config **first**, as a separate provisioning flow.
- The hardcoded schema IDs `1..8` in `configure_apps.py` are OpenCatalogi's,
  **not** ours (our schema id 2 is `subsidieverplichtingen…`, etc.).
- For the WOO-config functional test, the meaningful config-owned steps are
  **import + row counts + source credentials** — all now covered.

A future "OpenCatalogi base provisioning" test would: import the OpenCatalogi
base config, then run settings/catalog/home, resolving IDs by slug. Out of scope
for this repo's WOO config until that base config is available here.

---

## Original plan (retained for the OpenCatalogi-base flow)

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

- `make lint` / `make test` (18 unit tests) / `make functional` all green.
- `make functional` proven end-to-end on NC30 + openregister 1.0.3 /
  openconnector 0.2.20 / opencatalogi 1.0.3: import (17 schemas, 16 syncs) →
  row counts → credential provisioning (dummy key into `headers.API-KEY`).
- Source credentials for real sources come from a K8s secret / ESO via
  `provision.py --apikey-env`, never the config JSON. The config holds only an
  empty `headers.API-KEY` placeholder.
- Sync-trigger smoke test deliberately skipped: it would hit the live external
  demo source (`openwoo.zaaksysteem.net`); the plan calls for no real data fetch.
