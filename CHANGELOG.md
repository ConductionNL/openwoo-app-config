# Changelog

All notable changes to this repository are documented here.

## [Unreleased]

### Changed — 2026-06-15 (modularise the provisioner into a lib)
- Split the 1479-line `scripts/provision.py` monolith into the **`provisionlib`**
  package, each module small and separately auditable:
  - `constants.py` — every API path + provisioning default (pure literals)
  - `helpers.py` — pure, unit-tested helpers + `log` (no live stack)
  - `client.py` — the basic-auth JSON `Client` + `ProvisionError`
  - `steps.py` — the `provision_*` domain logic + `provision_all` orchestrator
  - `cli.py` — argparse wiring, secret resolution, `cmd_*` dispatch, `main`
- `scripts/provision.py` is now a **thin entrypoint** (delegates to
  `provisionlib.cli.main`); it inserts its own dir on `sys.path` so the GUI,
  webgui and `functional-test.sh` keep shelling out by path unchanged. The CLI
  surface (14 subcommands) is byte-for-byte the same — function bodies were moved
  verbatim, not rewritten.
- Callers can now `import provisionlib as provision` to reuse steps as a library;
  the package `__init__` re-exports the public surface.
- Tests: `tests/test_provision.py` now imports `provisionlib`; orchestrator tests
  patch `provision.steps.*` (the namespace `provision_all` resolves). No
  behavioural change — 84 passed, 1 skipped, same as before the split.

### Added — 2026-06-15 (delete-menu: remove OpenCatalogi default User Menu)
- **`provision.py delete-menu`** (+ step `[6/11]` in `all`): OpenCatalogi
  auto-creates a default **`User Menu`** object on the `publication` register; it
  does not belong in the WOO config (no per-tenant menu is shipped), so the
  provisioner removes it. GETs `publication/menu`, matches `User Menu`
  case-insensitively against each object's `name`/`title`/`slug`, DELETEs every
  match by `uuid` (falls back to `id`), then re-GETs the list and asserts the
  match is gone. **Idempotent**: skips with a log when absent. Override the match
  with `--menu-name`; skip the step in `all` with `--skip-delete-menu`.
- `Client.delete()` added (thin DELETE wrapper, mirrors `get`/`post`/`put`).
- Step numbering in `all` renumbered `/10` → `/11` (delete-menu inserted after
  `catalog`, before `credentials`).
- Tests: `_menu_matches` (name/title/slug, case-insensitive) + `provision_delete_menu`
  (delete by uuid, fallback to id, idempotent-when-absent, raises-if-still-present);
  orchestrator order test updated. 64 passed.

### Added — 2026-06-12 (rule source resolver + OpenRegister settings hardening)
- **`provision.py rules`** (+ step in `all`'s `sync-refs`): resolve each
  `fetch_file` rule's `configuration.fetch_file.source` slug → tenant numeric
  source id. Same forward-reference gap as the syncs (the import leaves it a slug
  or drops it on a fresh tenant, so the fetch_file action can't resolve the
  source). Idempotent. Confirmed live on canary 1.1.1 (`fetch_file.source=1 OK`).
  `BUG-import-forward-refs.md` extended to cover this case.
- **`provision_settings`** now also applies (idempotent, partial PUT merges):
  - `PUT /settings/retention` → **audit + search trails OFF**
    (`auditTrailsEnabled`/`searchTrailsEnabled` = false) — WOO syncs create many
    objects; the trails add overhead with little value here. Governance trade-off.
  - `PUT /settings/files` → **text extraction = `manual`** (`extractionMode`).
    Best-effort: the endpoint is OpenRegister 1.1.x+, skipped with a log on older
    tenants. Confirmed live on canary 1.1.1.
- **Not done — object text extraction (`objectExtractionMode` = manual):** lives in
  the `objectManagement` app value and is NOT settable via the settings API (the
  `/settings/objects` write endpoints are vectorization-only / return 405). Set it
  in the OpenRegister UI per tenant, or via `occ config:app:set`.
- Tests: 77 passed, 1 skipped (rule resolver ×3, unwrapped-settings ×1).

### Fixed — 2026-06-10 (btree index overflow on array fields)
- `attachments` and `values` (both `array`) were `facetable: true` in all 17
  schemas, so OpenRegister/MagicMapper put a **btree index** on the serialised
  array. A large attachments list overflowed Postgres' index-row limit on sync:
  `SQLSTATE[54000] index row requires 59520 bytes, maximum size is 8191`
  (gooisemeren.migrate). Faceting on a serialised array is meaningless anyway.
- Fix: `facetable: false` on `attachments` + `values` (34 properties, both arrays).
  `thema`/`titel` stay facetable (short, sensible facets). Lint 0/0.
- Note: like a column-type change, an existing index is not dropped in place —
  a tenant whose table already has the index needs a fresh table (re-wipe) for
  the fix to take effect.

### Added — 2026-06-10 (resolve synchronization slug references — fresh-tenant fix)
- New `provision.py syncs` step (and step `[7/10]` in `all`): after import, resolve
  each synchronization's slug references — `sourceId`, `sourceTargetMapping`,
  `actions` (rule slugs), `targetId` (`register/schema`) — to the tenant's numeric
  ids and PUT them, asserting they reflect. Idempotent (no-op once resolved).
- Why: the OpenRegister/OpenConnector import only resolves cross-object references
  against what already exists in the *same* pass, so on a **fresh** tenant a sync's
  forward references stay slugs and break at run time — e.g.
  `SQLSTATE[22P02] invalid input syntax for type bigint: "demo-xxllnc"` (sourceId
  still the source slug). Re-importing only shifts the problem (resolves syncs but
  re-slugs the jobs). This mirrors how `provision_jobs` already resolves a job's
  `synchronizationId`, so one import + the two resolve steps converge a clean tenant.
- Tests: 4 new (resolve all refs / idempotent / unknown-source / reflect-assert).
  73 passed, 1 skipped.

### Fixed — 2026-06-10 (samenvatting varchar(255) overflow on sync)
- All 17 WOO schemas defined `samenvatting` as `string` with `maxLength: 255`,
  which OpenRegister's MagicMapper materialises as a `varchar(255)` column. Real
  xxllnc data (e.g. a Woo-verzoek summary) exceeds 255 chars → the sync-run failed
  with `SQLSTATE[22001] value too long for type character varying(255)` when saving
  the object (confirmed in the canary nextcloud.log, table `openregister_table_2_17`).
- Fix: `samenvatting` → `format: "text"` (drop `maxLength`), matching the existing
  convention for long free-text fields (`titel`, `categorie`) so MagicMapper creates
  a TEXT column. Applied to all 17 schemas. Lint 0/0.
- Note: an existing `varchar(255)` column is not reliably widened in place; a tenant
  whose table was already created needs a fresh table (reset) for the TEXT column to
  take effect. Broadening the other free-text fields to `text` is a follow-up
  ("optimaliseren") once the targeted fix is confirmed on a clean canary.

### Changed — 2026-06-10 (Phase 3 deploy wiring — real image + namespace hardening)
- Image pinned to `docker.io/conduction2022/openwoo-provisioner:0.1.0` (built +
  pushed; keep the Docker Hub repo **private** — the image bundles `config/` +
  `scripts/`). `deployment.yaml` + `kustomization.yaml` updated.
- `namespace.yaml`: Pod Security `restricted` labels (matches platform convention;
  the deployment's securityContext complies).
- Argo wiring lives in **Nextcloud-base** (`nextcloud-platform/argo/`): an
  `Application` (`apps/openwoo-provisioner.yaml`) deploying `webgui/deploy` from
  Codeberg into `openwoo-platform`, plus the `openwoo-platform` destination added
  to the `nextcloud-platform` AppProject.
- SSO: the Google identity provider already exists in Keycloak (configured via UI);
  the realm import omits `identityProviders` so it is reused, not overwritten.

### Added — 2026-06-09 (web control-plane — Phase 3: Kubernetes deploy)
- `Dockerfile` (repo root) — control-plane image: `python:3.12-slim` + Flask +
  **gunicorn** (gthread, `--timeout 3600` so the streaming `/provision` log isn't
  cut), running the app **bound to `127.0.0.1:8081`**, non-root, unprivileged.
- `webgui/deploy/` — kustomize bundle for `https://platform.commonground.nu`:
  - `deployment.yaml` — **one pod, two containers**: the app (localhost only) +
    an **oauth2-proxy** sidecar that is the sole network listener (`:4180`) and
    forwards the authenticated identity to the app. Hardened securityContext
    (readOnlyRootFs, drop ALL caps, runAsNonRoot, seccomp RuntimeDefault).
  - `service.yaml` (ClusterIP `80→4180`), `ingress.yaml` (nginx + `letsencrypt-prod`
    TLS; buffering off + long read-timeout for streaming).
  - `networkpolicy.yaml` — pod ingress **only** from the `ingress-nginx` namespace
    on `:4180`; egress DNS + 443. With the localhost bind this **enforces in code**
    the "oauth2-proxy is the sole ingress" trust anchor (the Phase-2 review follow-up).
  - `oauth2-proxy.cfg` (moved here from `webgui/auth/`) → hashed ConfigMap via
    `configMapGenerator`; `secret.example.yaml` is a template (real Secret
    out-of-band); `argocd-application.example.yaml` for the GitOps repo.
  - `webgui/deploy/README.md` — build/push, prerequisites, apply, verify.
- `Makefile`: `image` / `push` / `k8s-validate` targets.
- Verified: `kustomize build` renders 7 resources (ConfigMap hash + volume
  rewrite); the image **builds and runs** — in-container `/healthz`=200, `/`
  without auth=**403 fail-closed**, `/` with `X-Forwarded-Email`=200.

### Added — 2026-06-09 (web control-plane — Phase 2: auth via oauth2-proxy → Keycloak)
- The web GUI is now **fronted by oauth2-proxy → Keycloak** (realm `commonground`,
  `iam.commonground.nu`), which **brokers Google** as identity provider — operators
  log in with Google, the app integrates only with Keycloak (OIDC).
- **App fails closed**: `server.py` now enforces `REQUIRE_AUTH` (default on) — every
  route except `/healthz` returns `403` without an `X-Forwarded-Email`/`-User`
  identity header. So a request that bypasses the proxy is refused, not served.
  The header is trustworthy only because oauth2-proxy is the **sole ingress**
  (app bound to localhost / NetworkPolicy). Local dev: `REQUIRE_AUTH=false`.
- `webgui/auth/oauth2-proxy.cfg` — proxy config (Keycloak OIDC upstream→Flask;
  `pass_user_headers`; `cookie_samesite=lax` closes the cross-site POST CSRF noted
  in the Phase-1 review). Secrets (`OAUTH2_PROXY_CLIENT_SECRET` / `_COOKIE_SECRET`)
  injected from env, never in the file.
- `webgui/auth/README.md` — trust model, the Keycloak `openwoo-provisioner` client +
  Google IdP to add (KeyCloak repo), required secrets, local fail-closed smoke test.
- Tests: 3 new auth-guard tests (403 unauthenticated, 200 with header, `/healthz`
  stays open). 8 webgui tests green under the venv; 69 + 1 skipped (system python).
- **KeyCloak repo** (separate, prod-path): adds the `openwoo-provisioner` OIDC
  client + Google identity provider to `realm-commonground.yaml`.

### Added — 2026-06-09 (hosted web control-plane — Phase 1: core, no auth)
- `webgui/` — a small **Flask** app that drives provisioning from **outside** the
  cluster against a tenant's **public URL**, reusing the tested
  `provision_gui.build_command()` (so the web form becomes `provision.py all`).
  - **Creds model A**: the operator enters the tenant password + source API key
    per run; the app **stores nothing**. Secrets reach the subprocess via **env**
    (`GUI_PROVISION_PASSWORD` / `GUI_PROVISION_APIKEY`), never argv — and are
    never logged (the audit line is `user + base + options` only).
  - Routes: `GET /` (form), `GET /healthz`, `POST /provision` (streams the
    `provision.py` step log back to the browser).
  - `current_user()` reads `X-Forwarded-Email`/`-User` — wired for the **Phase 2**
    oauth2-proxy → Keycloak (Google-brokered) front; **no auth in Phase 1** by
    design (run locally / behind a trusted network).
  - Files: `webgui/server.py`, `webgui/templates/index.html`,
    `webgui/requirements.txt` (`Flask>=3,<4`). Dev venv in `webgui/.venv/`
    (git-ignored).
  - Tests: `tests/test_webgui.py` (Flask test client) — `importorskip("flask")`
    so the system-python `make test` run **skips** it; run it under the venv
    (`webgui/.venv/bin/python -m pytest tests/test_webgui.py`). Verified
    end-to-end against canary (`POST /provision` → `FULL PROVISIONING OK`).

### Decided — 2026-06-09 (provisioning is operator-driven, not in-cluster)
- Provisioning runs from **outside** the cluster against a tenant's **public URL**
  (a trusted domain) via the CLI/GUI — not as in-cluster Argo Jobs. An in-cluster
  Argo path was prototyped (kustomize ConfigMap + per-tenant PostSync Job +
  ApplicationSet) and **removed**: the internal service Host (`nextcloud:8080`)
  isn't a trusted_domain (HTTP 400), and it created standing per-tenant Argo apps
  that weren't wanted. Removed `kustomization.yaml`, `deploy/`, `argocd/`,
  `Dockerfile`, and the `make image/push` targets. `provision.py all
  --skip-credentials` and `--host-header` remain (useful for any internal run).

### Changed — 2026-06-09 (idempotent — skip writes when already converged)
- Every write step now GET-checks first and **skips the write when the tenant is
  already in the desired state**, so a re-run on a converged tenant is a near
  no-op (only GETs). Applies to `settings`, `oc-settings`, `catalog`,
  `credentials`, and `jobs` (compares the *tenant's* current value, not the
  config slug). Assertions still run after any write.
- `import` is idempotent too: it runs `verify-import` first and **skips the
  upload when every config slug is already present**. `--force` (import) /
  `--force-import` (all) / GUI "Force re-import" re-upload anyway — needed when
  the config *content* changed (a slug-level check can't see that).
- Verified on canary: a second `all` run skips all steps (`0 job(s) updated`,
  "already present, skipping", etc.). 7 new unit tests.

### Added — 2026-06-09 (job-user workaround for the Anonymous-job bug)
- The `jobs` step now **always sets each job's `userId`** — defaulting to the
  admin `--user`, overridable with `--job-user <user>` (GUI: "Job user" field,
  blank = admin). Workaround for scheduled SynchronizationAction jobs running as
  `Anonymous` and being denied object writes
  (docs/BUG-sync-job-anonymous-permission.md); effective only if the runner
  honours `userId` (unverified — a manual authenticated run works regardless, so
  only the scheduled cron run proves it). The `jobs` step no longer skips a job
  when only the userId needs setting. 3 unit tests.

### Added — 2026-06-09 (Tkinter front-end)
- `scripts/provision_gui.py` — optional Tkinter form (tenant URL, admin user, app
  password, source URL, API-Interface-ID, source API key) that runs
  `provision.py all`, passing secrets via env (never argv) and streaming output.
  A **"Run synchronizations after provisioning"** checkbox adds `--run-syncs`
  (with a dry-run `--test` sub-option) so the syncs fire as part of the run.
  Pure-stdlib; with no Tkinter/display it falls back to printing the terminal
  command. Testable `build_command` core (4 unit tests).

### Added — 2026-06-09 (per-tenant source connection params)
- `provision.py credentials` / `all` now also set the source **`location` (URL)**
  and **`API-Interface-ID`** header alongside the API key — all three are
  per-tenant (each client's source system differs) and supplied at provision time
  (`--source-url` / `--api-interface-id` or interactive prompt; blank keeps the
  config default). Not committed to the config. The Argo reconciler handles the
  base config; these per-tenant source values are operator-supplied. 2 unit tests.
- `provision.py` is now fully interactive: omitting `--user` / `--password` /
  `--apikey` / `--source-url` / `--api-interface-id` prompts for them on a
  terminal (getpass for secrets), so `provision.py all --base <tenant>` asks for
  everything. Non-terminal runs still require flags/env.

### Added — 2026-06-09 (jobs synchronizationId resolution)
- `scripts/provision.py jobs` — after import, each job's `arguments.synchronizationId`
  is still the sync **slug** (the import does not resolve it, unlike sync
  `targetId`), but the `SynchronizationAction` needs the **numeric** sync id to
  trigger. This step resolves the sync slug → the tenant's numeric sync id and
  PUTs it onto the job, asserting it reflects. The config keeps the portable
  slug; the provisioner resolves it per tenant (like `catalog`). Added to the
  `all` flow (now 9 steps, after `sync-check`). Verified on canary: all 16 jobs'
  synchronizationId became numeric ids. 4 unit tests.

### Changed — 2026-06-09 (review + refactor, pre-Argo)
- Post-`/review` + `/security-review` cleanup (security review: no findings):
  - rewrote `scripts/provision.py`'s module header to describe all 11 subcommands
    + the idempotent-convergence model (was stale, only described `credentials`);
    refreshed the `import` / `authorization` / `all` `--help` strings.
  - `Client` now warns when `--base` is plain `http://` (non-localhost) — basic-auth
    would go in cleartext. Warning only, does not block.
  - documented that the list endpoints are assumed unpaginated (verify-import /
    catalog / oc-settings) so a future paginating API gets caught in review.
  - small: `config_source_slugs` delegates to `config_slugs`; dropped a redundant
    `import sys`.

### Changed — 2026-06-09 (simplified now that the import bug is fixed)
- OpenRegister **1.0.3** imports `authorization.inheritFromPublic` natively
  (the 0.2.3 bug is fixed — verified by an A/B clean test on canary: the raw
  config imports 17/17 schemas with the flag preserved in the DB). So:
  - `provision.py import` now uploads the config **as-is** (no strip).
  - the `authorization` step is **removed from the default `all` flow** and kept
    as a **standalone repair command** (e.g. flip `inheritFromPublic=false` on an
    existing tenant for department isolation). `all` is now 8 steps.
- `provision.py all` verified end-to-end on a freshly-reset canary: 17/17 schemas
  with `inheritFromPublic=true` in the DB **without** the restore step.

### Fixed — 2026-06-09 (handle inheritFromPublic instead of dropping it)
- Root cause of the 4 silently-dropped schemas (`adviezen`, `convenanten`,
  `wetten_en_algemeen_verbindende_voorschriften`, `woo_verzoeken_en_besluiten`):
  each carries `authorization.inheritFromPublic`. OpenRegister's **import**
  rejects that key and silently drops the schema (HTTP 200, `failed:[]`),
  leaving the 4 syncs dangling. Confirmed from the canary log
  (`[ImportHandler] ... Invalid authorization action 'inheritFromPublic'`) and
  reproduced from a clean NC 32.0.5 reinstall.
- `inheritFromPublic` is a **legitimate, intended** authorization flag (default
  `true`; setting it `false` isolates publications per department, e.g. Almere),
  so it is **kept in the config**, not stripped. The schema UPDATE API *does*
  accept it — only the import does not.
- Handling (provisioner): `provision.py import` strips `inheritFromPublic` for
  the import call so every schema lands, then `provision.py authorization`
  restores it via the schema UPDATE API and asserts it reflects.
  `scripts/functional-test.sh` now imports through `provision.py import`.
- `scripts/oac.py`: **bad-authorization** lint check now allows the
  `inheritFromPublic` flag alongside the create/read/update/delete actions, and
  flags any *other* unrecognised authorization key.
- Verified A-to-Z on a clean canary: import → 17/17 schemas → authorization sets
  `inheritFromPublic=true` on the 4 (confirmed in the DB) → catalog links all 17
  → sync-check clean.

### Changed — 2026-06-08 (orchestrator)
- `scripts/provision.py all` now runs the full bring-up: settings → **oc-settings**
  → verify-import → **catalog** → credentials → sync-check → (optional sync-run).
  `--skip-oc-settings` / `--skip-catalog` for a WOO-only tenant without the
  OpenCatalogi base. Proven end-to-end on canary (all steps green).

### Added — 2026-06-08 (OpenCatalogi settings)
- `scripts/provision.py oc-settings` — couples each OpenCatalogi object type
  (catalog/listing/organization/theme/page/menu/glossary) to its register +
  schema via `POST /apps/opencatalogi/api/settings`, resolving the `publication`
  register slug and each type's same-named schema slug to tenant ids, then
  asserting the coupling reflects. Makes the coupling reproducibly owned by the
  provisioner instead of relying on the base install. Proven on canary; 3 unit
  tests. (Default-organisation is left on `auto_create_default_organisation`;
  multitenancy is set to disabled by the `settings` step.)

### Added — 2026-06-08 (catalog)
- `scripts/provision.py catalog` — points the OpenCatalogi `publications` catalog
  object at the WOO register + **all** its schemas, resolving the register and
  schema slugs to tenant ids (the object stores numeric ids) and asserting they
  reflect. 3 unit tests. Proven on canary (17 schemas linked).

### Added — 2026-06-08 (jobs)
- `config/woo.configuration.json` now carries the **16 synchronization jobs**
  (one `SynchronizationAction` per sync, `interval` 1800s) that the OpenRegister
  configuration export omits. Sourced from the toolchain reference config — the
  only structural difference between it and our config was these jobs (all other
  buckets had identical slug sets). Each job is **sanitized** (runtime fields
  stripped) and made **portable**: `arguments.synchronizationId` was rewritten
  from the instance-specific numeric id to the synchronization **slug**.
- `scripts/oac.py`: added a `jobs` runtime strip-set (`executionTime`,
  `jobListId`, `lastRun`, `nextRun`, `reference`, `status`, `userId`, `version`)
  and a reference-integrity check that every `SynchronizationAction` job's
  `synchronizationId` resolves to a synchronization slug (a numeric/unknown ref
  is non-portable and now fails lint). 3 unit tests.
- Verified against canary: the jobs-bearing config imports HTTP 200 and creates
  all 16 jobs with the slug reference preserved. (Local `make functional` was
  separately blocked by an appstore download failure for the openregister app,
  unrelated to this change.)

### Added — 2026-06-08 (full-flow orchestrator)
- `scripts/provision.py all` — runs the bring-up in order and gates on each step:
  settings → verify-import → credentials → sync-check → (optional `--run-syncs`).
  Stops at the first failed assertion. Object creation and job runs stay separate.
  Proven against canary: it correctly halts at verify-import on the partial-import
  bug (4 schemas missing) rather than proceeding. 4 unit tests for the sequencing.
- `verify-import` now also covers the `jobs` bucket (skipped while the config has
  no jobs; active once jobs land after the hotfix).

### Added — 2026-06-08 (sync-run + objects)
- `scripts/provision.py sync-run` — POST `…/api/synchronizations/{id}/run` (or
  `--test` for the `/test` dry-run) for every config synchronization, resolved
  by slug, asserting no error. A real run fetches live data from the source, so
  it targets a real tenant, not the local CI test. Endpoints confirmed from the
  OpenConnector source (`synchronizations#run` / `#test`).
- `scripts/provision.py objects` — create one object in a register/schema
  (`POST …/api/objects/{register}/{schema}`) from a JSON payload file and assert
  the response carries an id/uuid.
- 6 unit tests (run vs test endpoint, error/ missing-sync paths, object id assert).
- Live-testing of these two is deferred until after the OpenRegister import
  hotfix (the partial-import bug leaves canary's syncs dangling until then).

### Added — 2026-06-08 (settings provisioning)
- `scripts/provision.py settings` — PUT `…/settings/organisation` (relies on
  `auto_create_default_organisation` by default; `--default-organisation` to pin
  a UUID) and `…/settings/multitenancy` (disabled by default, `--multitenancy`
  to enable), then GET each back and assert the sent fields reflect. Proven
  against canary. 2 unit tests.

### Added — 2026-06-08 (tenant verification)
- `scripts/provision.py verify-import` — after an import, compares the config's
  slugs (registers/schemas/sources/synchronizations) against what is actually
  present on the tenant and fails on any missing entity. The import API returns
  HTTP 200 even when it silently drops rows, so on a tenant that already held
  data the bulk row count cannot see the gap; this slug-level diff can.
- `scripts/provision.py sync-check` — asserts every config synchronization
  resolved its target schema on the tenant (targetId rewritten to a numeric
  `register/schema` id), flagging any left dangling as `register/<slug>`.
- Both proven against canary, where they caught a real OpenRegister import bug:
  importing this config into a non-empty instance (8 pre-existing OpenCatalogi
  base schemas) silently created only 13 of 17 WOO schemas — leaving 4 schemas
  absent and their 4 synchronizations dangling — while returning HTTP 200
  "Import successful". Confirmed via the canary DB: the 4 are genuinely not
  created (not soft-deleted / org-filtered). See `docs/PROVISIONING-TEST-PLAN.md`.
- 9 more unit tests (slug diff, dangling-target detection, config-scoped filtering).

### Added — 2026-06-08 (provisioning / credentials)
- `scripts/provision.py` — post-import tenant provisioner (pure stdlib, urllib).
  `credentials` subcommand: resolves every config source by slug on the running
  instance, sets the `headers.API-KEY` entry in the source `configuration`
  (read-modify-write, preserving existing headers like `API-Interface-ID`), then
  GETs the source back and asserts the key reflected. Key comes from `--apikey`
  / `--apikey-env` (never logged); a clearly-marked dummy is used when none is
  supplied (CI / local test).
- `scripts/functional-test.sh` — new `provision_credentials` step after the
  row-count check; proves the credential-provisioning path end-to-end (dummy
  key). Verified on NC30 + openconnector 0.2.20.
- `tests/test_provision.py` — 11 unit tests (slug resolution, header merge /
  config preservation, dummy vs supplied key, missing-source and
  no-reflection failure paths).
- `config/woo.configuration.json` — source `demo-xxllnc` gains an **empty**
  `configuration."headers.API-KEY"` placeholder. The real demo key is injected
  at provision time from a secret / env var and is never committed.

### Finding — 2026-06-08
- OpenCatalogi settings / default-catalog / home-page provisioning steps operate
  on OpenCatalogi's **own** entities (a `publication` register, `catalog`/
  `listing`/… schemas) which are NOT in the WOO config — a fresh import yields
  only register `woo` + 17 WOO schemas. Those steps require a separate
  OpenCatalogi-base provisioning flow and are out of scope for this repo's WOO
  config. Details in `docs/PROVISIONING-TEST-PLAN.md`.

### Added — 2026-06-08
- Initial scaffold of the OpenWoo config validation repo.
- `scripts/oac.py` — pure-stdlib linter + sanitizer for OpenRegister
  configuration exports. Detects and strips "export-on-import-on-postgres"
  runtime pollution (sync cursors, content hashes, last-synced timestamps,
  source rate-limit counters / `lastCall` / `objectCount`, register `usage`,
  created/updated metadata). Per-bucket strip-set: `schemas[*].version` is
  preserved (semantic); behavioural flags (`published`, `deleted`, `owner`,
  `rateLimitLimit/Window`, `isEnabled`) are never auto-stripped.
- Reference-integrity checks: `synchronizations[*].sourceId` and `targetId`
  must resolve to existing sources / registers / schemas.
- `schema/openregister-config.schema.json` — structural envelope contract.
- `tests/test_oac.py` — unit tests (pollution, schema-version preservation,
  dangling refs, sanitize idempotency, data-leak warning).
- `.woodpecker.yml` — Codeberg CI: lint + tests + gitleaks secret scan.
- `docker-compose.test.yml` + `scripts/functional-test.sh` — Layer-2 functional
  test: ephemeral Nextcloud + PostgreSQL, installs the Conduction apps, imports
  the config via `POST /apps/openregister/api/configurations/import` and asserts
  success, then **verifies PostgreSQL row counts** (registers/schemas/sources/
  mappings/rules/synchronizations) against the config — the import returns
  HTTP 200 even when its response omits created rows, so the count check is the
  real proof. Auto-detects compose (docker/podman). Local-only (Codeberg runners
  provide buildah, not docker/compose); no live credentials. `make functional`.
  Verified end-to-end: a fresh NC30 + openregister 1.0.3 / openconnector 0.2.20 /
  opencatalogi 1.0.3 imports the WOO config with all 17 schemas + 16 syncs.
- README: mandatory contribution workflow — every config change goes through
  this repo (branch → sanitize → lint/test → functional → PR → tag).
- `Makefile` — source of truth for local + CI commands.
- `config/woo.configuration.json` — initial canonical config, sanitized from
  `configuration_7_2026-06-08.json` (261 runtime fields stripped).
