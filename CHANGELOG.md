# Changelog

All notable changes to this repository are documented here.

## [Unreleased]

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
