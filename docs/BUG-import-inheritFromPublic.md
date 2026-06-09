# Bug: config import rejects `authorization.inheritFromPublic` and silently drops the schema

**Component:** OpenRegister — `lib/Service/Configuration/ImportHandler.php` (Pass 1, ~line 1380)
**Severity:** high (silent data loss on import; HTTP 200 hides it)
**Found:** 2026-06-08/09, canary.accept.commonground.nu, Nextcloud 32.0.5, OpenRegister **0.2.3**
**Status:** ✅ **FIXED in OpenRegister 1.0.3** (verified on canary 2026-06-09).

## Fix verification (2026-06-09)

On OpenRegister **1.0.3**, an A/B clean test (openregister/openconnector tables
emptied, OpenCatalogi base kept) showed the **raw** config — with
`authorization.inheritFromPublic` left in — imports **all 17 schemas** and
preserves `inheritFromPublic: true` in `oc_openregister_schemas` (confirmed in
the DB). No schemas dropped. So the import now accepts the flag.

Consequence for this repo: the `provision.py import` strip + `authorization`
restore workaround is **no longer required on 1.0.3+** (kept only as defensive
support for older deployments). Everything below describes the original 0.2.3 bug.

---

## Summary

When a schema's `authorization` object contains the boolean flag
`inheritFromPublic`, the configuration **import** rejects the whole schema with:

```
[ImportHandler] Failed to create schema (Pass 1)
Failed to import schema: Invalid authorization action 'inheritFromPublic'.
Must be one of: create, read, update, delete
```

The schema is then **not created**, but the import endpoint still returns
**HTTP 200 `{"message":"Import successful"}`** with `workflows.failed: []` — so
the caller has no signal that anything was dropped. Synchronizations whose
`targetId` points at the dropped schema are left dangling (`<register>/<slug>`
never resolves to a numeric id).

## The asymmetry (why this is a bug, not config)

The schema **UPDATE API accepts the exact same key**:

```
PUT /index.php/apps/openregister/api/objects?... /api/schemas/{id}
body: {"authorization": {"read": [...], "inheritFromPublic": false}}
-> 200, GET back shows authorization.inheritFromPublic = false
```

So `inheritFromPublic` is a legitimate authorization field (default `true`;
`false` isolates publications per department, e.g. Almere). Only the *import*
path validates `authorization` keys as if they were all actions.

## Root cause (likely)

The import iterates the `authorization` object's keys and validates each against
the action set {create, read, update, delete}. It does not exempt the boolean
flag(s) (`inheritFromPublic`) that the rest of the app treats as valid.

## Suggested fix

In the import's authorization validation, treat `inheritFromPublic` (and any
other boolean flags the schema model accepts) as valid keys rather than actions
— mirror whatever the schema UPDATE path does. Also: when a schema fails in
Pass 1, surface it in the response (`workflows.failed`) instead of returning a
bare "Import successful".

## Reproduce

1. Fresh Nextcloud + OpenRegister + OpenCatalogi base (1 register, 8 schemas).
2. Import a config whose schemas carry `authorization.inheritFromPublic`.
3. Import returns 200 "Import successful", but those schemas are absent
   (`GET /api/schemas`), confirmed in `oc_openregister_schemas` (0 rows for the
   slugs), and their syncs show `targetId = <register>/<slug>` unresolved.

## Related: jobs `synchronizationId` not resolved on import

Separate but adjacent: the import resolves a synchronization's `targetId`
(schema slug → numeric id) but does **not** resolve a job's
`arguments.synchronizationId` — it leaves the sync **slug** there, while the
`SynchronizationAction` needs the numeric sync id to trigger. Ideally the import
resolves this the same way it resolves `targetId`. Workaround: `provision.py
jobs` resolves the sync slug → numeric id and PUTs it onto each job.

## Workaround (this repo)

`scripts/provision.py import` strips `inheritFromPublic` for the upload so every
schema lands, then `scripts/provision.py authorization` restores it via the
schema UPDATE API and asserts it reflects. `scripts/oac.py` lints the key as
valid (so the config keeps it). Remove the workaround once the import is fixed.
