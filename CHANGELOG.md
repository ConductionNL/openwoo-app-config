# Changelog

All notable changes to this repository are documented here.

## [Unreleased]

### Fixed — 2026-06-08 (root cause of the silent partial import)
- **4 schemas** (`adviezen`, `convenanten`, `wetten_en_algemeen_verbindende_voorschriften`,
  `woo_verzoeken_en_besluiten`) carried an invalid `authorization` key
  `inheritFromPublic: true`. OpenRegister's import rejects any authorization key
  that is not `create`/`read`/`update`/`delete` and then **silently drops the
  whole schema** (HTTP 200, no failure in the response), which also left the 4
  matching synchronizations dangling. Confirmed from the canary Nextcloud log
  (`[ImportHandler] Failed to create schema (Pass 1) ... Invalid authorization
  action 'inheritFromPublic'`). Removed the stray flag from the 4 schemas.
- `scripts/oac.py`: new **bad-authorization** lint check — a schema
  `authorization` key that is not a valid action now fails the gate (would have
  caught this before it reached a tenant). 2 unit tests.
- Verified on a clean canary (NC 32.0.5): the fixed config imports **17/17
  schemas**, the catalog links all 17, and `sync-check` reports no dangling.

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
