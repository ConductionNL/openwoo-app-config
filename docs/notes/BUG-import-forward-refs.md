---
last_reviewed: 2026-07-06
owner: mark
---

# BUG: config import does not resolve forward-reference slugs to ids

**Status:** open — workaround in `provision.py` (`syncs` + `jobs` steps).
**Components:** OpenRegister / OpenConnector configuration import.
**Severity:** high for portable config — the config object cannot stand alone.

## Summary

The configuration import resolves a cross-object reference only against entities
that **already exist at the moment each entity is created**, within the same
import pass. A **forward reference** — an entity that points to another entity
created *later* in the same import — is left as the **slug** instead of being
resolved to the target's numeric id. On a fresh tenant this breaks at run time.

## Symptoms (fresh tenant, single import)

- A **synchronization**'s `sourceId` stays the source *slug* (e.g. `demo-xxllnc`)
  instead of the numeric source id. Running the sync fails:
  ```
  SQLSTATE[22P02]: Invalid text representation: invalid input syntax for type
  bigint: "demo-xxllnc"
  ```
  (the runner queries the source by id, but the id column is a bigint and gets a
  slug). The same applies to `sourceTargetMapping`, `actions` (rule slugs) and
  `targetId` (`register/schema`).
- A **job**'s `arguments.synchronizationId` stays the sync *slug*, so the
  SynchronizationAction cannot trigger the numeric sync id.
- A **rule**'s `configuration.fetch_file.source` stays the source *slug*
  (`demo-xxllnc`) — or is dropped entirely on some tenants — so the `fetch_file`
  action cannot resolve the source to fetch attachments from.

## Why "just import twice" does not work

Re-importing only **shifts** the problem. The second pass resolves the
synchronizations (the source now exists with a numeric id), but it **re-imports
the jobs** with the config slug, so the jobs go back to an unresolved
`synchronizationId`. There is no single number of passes that leaves both syncs
and jobs resolved.

## Repro

1. Fresh tenant (no prior import).
2. Import `config/woo.configuration.json` once (HTTP 200, "Import successful").
3. Run any synchronization → HTTP 400, `SQLSTATE[22P02] ... bigint: "demo-xxllnc"`.
   (Confirmed on canary, NC 32.0.5, `openregister_table_2_17`.)

## Current workaround (this repo)

`scripts/provision.py` resolves the references **after** a single import, against
the final numeric ids — idempotent, slug-matched:
- `provision.py syncs` — resolves each synchronization's `sourceId`,
  `sourceTargetMapping`, `actions`, `targetId`.
- `provision.py jobs` — resolves each job's `arguments.synchronizationId`.
- `provision.py rules` — resolves each `fetch_file` rule's
  `configuration.fetch_file.source` (run inside the `all` `sync-refs` step).

`provision.py all` runs one import then both resolve steps, which converges a
clean tenant (verified end-to-end: `FULL PROVISIONING OK`, all 16 syncs run).

## Proposed fix (devs)

Make the import resolve all slug references to numeric ids **within the import**,
e.g. a two-phase import: (1) create/upsert every entity, (2) resolve all
cross-references (sync→source/mapping/rule/register/schema, job→sync) now that
every id is known. Alternatively, accept slugs at **run time** (resolve slug→id
when a sync/job runs). Either makes the exported config object self-activating —
import it on a fresh tenant and it works, with only per-tenant credentials added
out of band.
