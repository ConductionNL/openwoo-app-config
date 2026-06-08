# openwoo-app-config

Versioned, **validated** OpenRegister configuration for the OpenWoo app (Woo
register, schemas, mappings, sources, synchronizations).

This repo exists for one reason: the config is a ~14k-line JSON document that
devs hand-edit and re-export, and mistakes in it silently break app
functionality. Here the config is version-controlled and **gated by CI** before
it is ever loaded into a tenant.

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
| `schema/openregister-config.schema.json` | Structural envelope contract |
| `tests/test_oac.py` | Unit tests for the linter/sanitizer |
| `.woodpecker.yml` | Codeberg CI (lint + tests + secret scan) |

Zero third-party dependencies is deliberate: full auditability, no supply-chain
surface, reproducible anywhere `python3` exists.

## Usage

```bash
make lint                 # CI gate: fail on runtime pollution / dangling refs
make sanitize             # strip pollution from config/woo.configuration.json in place
make sanitize RAW=fresh-export.json   # clean a fresh export into the canonical config
make test                 # run unit tests
```

**Workflow when you have a new export:** drop it in the repo, run
`make sanitize RAW=<file>`, review the diff, commit.

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
  every `targetId` of the form `register/schema` resolves to a real register + schema.
- **data-leak** (warn) — stored `objects` in a config export.

## How Nextcloud-base consumes this

`Nextcloud-base` (the GitOps platform) does **not** own this config. It consumes
a tagged, validated version of `config/woo.configuration.json` — the same way it
pins app versions. Config errors are caught in *this* repo's CI, before they can
reach a tenant.
