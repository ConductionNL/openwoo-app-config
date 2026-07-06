---
last_reviewed: 2026-07-06
owner: mark
---

# Sanitizer & linter reference

## What gets stripped, and why

`scripts/oac.py` strips only **entity top-level** keys (never recurses
into schema property definitions), per bucket:

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
> 1. The `schemas`/`version` exception — stripping `version` globally
>    would destroy real schema versions. That's why the strip-set is
>    per-bucket.
> 2. Behavioural/config flags (`published`, `depublished`, `deleted`,
>    `owner`, `rateLimitLimit`, `rateLimitWindow`, `isEnabled`) are
>    **never** auto-stripped — they may carry meaning. See
>    `BUCKET_RUNTIME_KEYS` in `scripts/oac.py`.

`objects` (stored data records) should be empty in a config export. A
non-empty `objects` bucket produces a **warning** — but the sanitizer
never deletes data automatically.

## Checks the linter enforces (gate)

- **structure** — `openapi`, `info`, `components` present.
- **pollution** — no runtime fields leaked into entities (the
  postgres-import bug).
- **dangling-ref** — every `synchronizations[*].sourceId` resolves to a
  `source`; every `targetId` of the form `register/schema` resolves to a
  real register + schema; every `SynchronizationAction` job's
  `synchronizationId` resolves to a synchronization slug.
- **bad-authorization** — every schema `authorization` key is a valid
  action (`create`/`read`/`update`/`delete`) or the `inheritFromPublic`
  flag. Any other key is unrecognised and fails the gate.
  (`inheritFromPublic` imports natively on OpenRegister 1.0.3+;
  OpenRegister 0.2.3 rejected it.)
- **data-leak** (warn) — stored `objects` in a config export.
