# openwoo-app-config

Versioned, **validated** OpenRegister configuration for the OpenWoo app (Woo
register, schemas, mappings, sources, synchronizations).

This repo exists for one reason: the config is a large JSON document
(~7,500 lines pretty-printed) that devs hand-edit and re-export, and mistakes in
it silently break app functionality. Here the config is version-controlled and
**gated by CI** before it is ever loaded into a tenant.

## The problem this solves

The config is an OpenRegister *configuration export* — an OpenAPI-enveloped JSON
document (`openapi` / `info` / `components` / `x-openregister`) whose
`components` hold the config buckets: `registers`, `schemas`, `mappings`,
`sources`, `rules`, `synchronizations`, `endpoints`, `jobs`, `workflows`,
`objects`.

When an export is taken from an instance that has **imported into PostgreSQL**,
runtime state (sync cursors, content hashes, last-synced timestamps,
created/updated metadata) leaks back into the export and pollutes the config.
That noise causes broken diffs and unpredictable imports.

## What's in here

| Path | What |
|------|------|
| `config/woo.configuration.json` | The canonical, **sanitized** config (commit this) |
| `scripts/oac.py` | Linter + sanitizer — pure stdlib Python, **zero dependencies** |
| `scripts/provision.py` | Post-import provisioner — sets source API-key credentials over the API, stdlib only |
| `scripts/functional-test.sh` | Layer-2 functional test (ephemeral Nextcloud import + provision) |
| `schema/openregister-config.schema.json` | Structural envelope contract |
| `tests/test_oac.py` | Unit tests for the linter/sanitizer |
| `tests/test_provision.py` | Unit tests for the provisioner |
| `.woodpecker.yml` | Codeberg CI (lint + tests + secret scan) |

Zero third-party dependencies is deliberate: full auditability, no supply-chain
surface, reproducible anywhere `python3` exists.

## Two tracks: source validation and target configuration

This repo works along two independent tracks:

| Track | Question it answers | Tooling | Needs a tenant? |
|-------|---------------------|---------|-----------------|
| **Source** — config validation | Is the config artefact correct and portable? | `scripts/oac.py` (`lint` / `sanitize`) + `tests/` | no — runs on the file |
| **Target** — configuration, validation & repair | Is a running tenant in the desired state, and bring it there | `scripts/provision.py` | yes — points at a tenant URL |

The **source track** is the CI gate: pollution, dangling refs and bad
authorization keys are caught on the JSON before it can reach a tenant. The
**target track** drives a real tenant over the API and asserts each step — some
steps *validate* (`verify-import`, `sync-check`), others *configure or repair*
(`settings`, `oc-settings`, `import`, `authorization`, `catalog`, `credentials`).

### Pointing the target track at a tenant (handover)

Any operator who can reach a tenant can run the target track — it is pure-stdlib
Python over HTTPS, no repo-specific state. Supply the tenant URL and an admin /
app-password credential (kept out of argv via env):

```bash
# one .env file per target (gitignored; see .env.canary.example)
#   CANARY_USER=...      CANARY_PASS=<app password>      OPENWOO_APIKEY=<source key>
set -a; . .env.<target>; set +a
python3 scripts/provision.py all \
    --base https://<tenant> --user "$CANARY_USER" \
    --password-env CANARY_PASS --apikey-env OPENWOO_APIKEY
```

Credentials can also come from an **interactive prompt** — omit `--password` /
`--password-env` and, on a terminal, the tool asks for the app password
(`getpass`, never stored or in argv):

```bash
python3 scripts/provision.py all \
    --base https://<tenant> --user admin --apikey-env OPENWOO_APIKEY   # prompts for the password
```

`all` is a **convergence/repair** run: it updates the existing tenant's objects to
the config's desired state (idempotent upserts) — it does not wipe, and does not
prune entities that exist on the tenant but not in the config. Run a single step
(`verify-import`, `sync-check`, …) to *validate* an existing tenant without
changing it. Credentials come from the operator's own env/secret, never from this
repo.

## The rule: every config change goes through this repo

The OpenWoo config is **not** edited live in a tenant and is **not** imported
from a hand-edited export. The single source of truth is
`config/woo.configuration.json` here. Anyone changing the config follows this
flow — no exceptions:

1. `git switch -c feat/<what-changed>`
2. Make/obtain a fresh export → drop it in the repo as `raw-<date>.json`
   (git-ignored, never committed).
3. `make sanitize RAW=raw-<date>.json` — strips the postgres runtime pollution
   into `config/woo.configuration.json`.
4. `make lint && make test` — the gate. Fix any dangling refs / pollution.
5. `make functional` *(local)* — prove it imports into a clean Nextcloud.
6. Review the diff, open a PR. CI runs `lint` + `test` + secret scan.
7. Merge → tag a release. Nextcloud-base consumes the tag (see below).

This is the whole point of the repo: errors are caught here, in review and CI,
before the config can reach a tenant.

## Usage

```bash
make lint                 # CI gate: fail on runtime pollution / dangling refs
make sanitize             # strip pollution from config/woo.configuration.json in place
make sanitize RAW=fresh-export.json   # clean a fresh export into the canonical config
make test                 # run unit tests
make functional           # local layer-2: import into ephemeral Nextcloud (needs docker)
```

## What gets stripped, and why

`scripts/oac.py` strips only **entity top-level** keys (never recurses into
schema property definitions), per bucket:

| Bucket | Stripped (on top of timestamps) | Kept |
|--------|----------|------|
| all buckets | `created`, `updated`, `dateCreated`, `dateModified` | everything else |
| `synchronizations` | `currentPage`, `sourceHash`, `targetHash`, `source/targetLast{Changed,Checked,Synced}`, `status`, `version` | `name`, `slug`, `sourceId`, `targetId`, mappings, conditions, actions, configurations |
| `sources` | `lastCall`, `lastSync`, `objectCount`, `rateLimitRemaining`, `rateLimitReset`, `status`, `version` | `location`, `type`, `auth`, `headers`, `rateLimitLimit`, `rateLimitWindow`, config |
| `registers` | `usage`, `version` | `slug`, `title`, `schemas`, `quota`, config |
| `mappings`, `rules` | `version` | definition fields |
| `jobs` | `executionTime`, `jobListId`, `lastRun`, `nextRun`, `reference`, `status`, `userId`, `version` | `name`, `jobClass`, `arguments`, `interval`, `isEnabled`, `slug` |
| `schemas` | timestamps only | **`version` is preserved** — semantic schema version (e.g. `1.0.4`), not runtime noise |

> **Two non-obvious rules:**
> 1. The `schemas`/`version` exception — stripping `version` globally would
>    destroy real schema versions. That's why the strip-set is per-bucket.
> 2. Behavioural/config flags (`published`, `depublished`, `deleted`, `owner`,
>    `rateLimitLimit`, `rateLimitWindow`, `isEnabled`) are **never** auto-stripped
>    — they may carry meaning. See `BUCKET_RUNTIME_KEYS` in `scripts/oac.py`.

`objects` (stored data records) should be empty in a config export. A non-empty
`objects` bucket produces a **warning** — but the sanitizer never deletes data
automatically.

## Checks the linter enforces (gate)

- **structure** — `openapi`, `info`, `components` present.
- **pollution** — no runtime fields leaked into entities (the postgres-import bug).
- **dangling-ref** — every `synchronizations[*].sourceId` resolves to a `source`;
  every `targetId` of the form `register/schema` resolves to a real register + schema;
  every `SynchronizationAction` job's `synchronizationId` resolves to a synchronization slug.
- **bad-authorization** — every schema `authorization` key is a valid action
  (`create`/`read`/`update`/`delete`) or the `inheritFromPublic` flag. Any other
  key is unrecognised and fails the gate. (`inheritFromPublic` imports natively on
  OpenRegister 1.0.3+; OpenRegister 0.2.3 rejected it — see
  `docs/BUG-import-inheritFromPublic.md`.)
- **data-leak** (warn) — stored `objects` in a config export.

## Layer 2 — functional import test (local)

Static `lint` cannot prove the config actually *loads*. `make functional` does:
it spins up an **ephemeral** Nextcloud + PostgreSQL (`docker-compose.test.yml`),
installs the Conduction apps, and imports the config through the real
OpenRegister API:

```
POST /apps/openregister/api/configurations/import   (multipart file=)
```

The stack is wiped after every run (`down -v`), so the test always proves a
**clean** tenant accepts the config — the same starting point a new tenant has.

It then **verifies row counts** in PostgreSQL against the config: registers,
schemas, sources, mappings, rules and synchronizations must all match. This is
deliberate — the import API returns `HTTP 200 "Import successful"` even when its
response body omits the created rows (e.g. it echoes `schemas: []` while 17
schemas were in fact written), so a status-code check alone is not proof. The
count check is.

After the row-count check, the test runs the **post-import provisioner**
(`scripts/provision.py`): for every source in the config it sets the API-key
header on the running instance and asserts it reflects — proving the
credential-provisioning path without performing any real data fetch. In the test
it writes a clearly-marked dummy key.

```bash
make functional                  # full cycle: up → install → import → assert → provision → teardown
KEEP_UP=1 make functional        # leave the stack at http://localhost:8080 to debug
```

### The provisioner (`scripts/provision.py`)

A real tenant bring-up is more than the import. `provision.py` performs the
post-install steps the config owns, over the API, each asserting it took effect
("test what you ship"). Every step is one subcommand with its own unit tests:

| Subcommand | Does | Asserts |
|------------|------|---------|
| `settings` | PUT organisation + multitenancy settings | GET reflects the sent fields |
| `import` | upload the config | response reports "Import successful" |
| `authorization` | (repair) set/flip schema authorization flags (`inheritFromPublic`) on a tenant | the flag reflects on each schema |
| `oc-settings` | couple OpenCatalogi object types to their register + schema | GET reflects (slugs resolved to tenant ids) |
| `verify-import` | compare config slugs to the tenant | every register/schema/source/sync present |
| `sync-check` | inspect tenant synchronizations | every target schema resolved (no dangling `reg/<slug>`) |
| `credentials` | set each source's `headers.API-KEY` | GET reflects the key |
| `sync-run` | POST run/`--test` per synchronization | no error (real run fetches live data) |
| `jobs` | resolve each job's `synchronizationId` (sync slug → tenant numeric id) | the job reflects the numeric id |
| `objects` | create one object in a register/schema | response carries an id/uuid |
| `catalog` | point the OpenCatalogi catalog at the WOO register + all its schemas | registers/schemas reflect (slugs resolved to tenant ids) |
| `all` | run the bring-up in order, gating each step | settings → verify-import → credentials → sync-check → (`--run-syncs`) |

`verify-import` and `sync-check` exist because the import API returns HTTP 200
even when it silently drops rows: on a tenant that already holds data the bulk
row count can't see the gap, but a slug-level diff can. (They caught exactly
this on canary — see the test plan.)

Connection flags are shared: `--base`, `--user`, and `--password` /
`--password-env` (the env form keeps the secret out of argv). Steps that read the
config also take `--config`.

### Source credentials (provisioning)

The demo source authenticates with an API key sent as the `API-KEY` request
header. The config ships an **empty placeholder** (`configuration."headers.API-KEY": ""`)
— the real key is **never committed**; it is injected at provision time from a
secret (a K8s secret / ESO in prod, an env var locally):

```bash
# real provisioning against a tenant — password and key come from env vars
# (kept out of argv / logs); username is not a secret:
python3 scripts/provision.py credentials \
    --base https://<tenant> --user <admin> \
    --password-env CANARY_PASS --apikey-env OPENWOO_APIKEY
```

Real credentials for any source come from a secret store, never from the config
JSON or git history.

> **Why not on Codeberg CI:** Codeberg's shared runners only provide buildah,
> not docker/compose, so this stack can't run there. Layer 2 is therefore a
> local check (and a candidate for a self-hosted nightly), while the static
> `lint`/`test` gate runs on every push. Auth uses the ephemeral container's own
> admin — **no live credentials live in Codeberg.**

Possible extension (not yet wired): round-trip — after import, `GET
.../configurations/{id}/export`, run it back through `sanitize`, and assert the
register/schema/source/sync slug-set matches the input. That would also prove the
sanitizer captures every runtime field OpenRegister emits.

## How Nextcloud-base consumes this

`Nextcloud-base` (the GitOps platform) does **not** own this config. It consumes
a tagged, validated version of `config/woo.configuration.json` — the same way it
pins app versions. Config errors are caught in *this* repo's CI, before they can
reach a tenant.
