---
last_reviewed: 2026-07-06
owner: mark
---

# Bug: scheduled synchronization jobs run as `Anonymous` and are denied object writes

**Component:** OpenConnector — `OCA\OpenConnector\Action\SynchronizationAction`
(scheduled job runner) + OpenRegister RBAC on object writes.
**Severity:** high — scheduled syncs cannot write fetched data; only 0-result
syncs appear to "succeed".
**Found:** 2026-06-09, hofvantwente.accept.commonground.nu, NC 32.0.5,
openconnector 0.2.20 / openregister 1.0.3.

## Symptom

A scheduled `SynchronizationAction` job logs:

```json
{
  "level": "ERROR",
  "message": "Failed to synchronize: User 'Anonymous' does not have permission to 'update' objects",
  "jobClass": "OCA\\OpenConnector\\Action\\SynchronizationAction",
  "arguments": {"synchronizationId": 2},
  "userId": null,
  "stackTrace": ["Check for a valid synchronization ID", "Getting synchronization: 2", "Doing the synchronization", ...]
}
```

`userId` is **null** — the job runs as the `Anonymous` user. With OpenRegister
RBAC enabled (`rbac.enabled: true`, `anonymousGroup: public`), `Anonymous`/public
may `read` objects but not `create`/`update` them, so writing fetched records is
denied.

## Why it looks intermittent ("some jobs work, others don't")

It is data-dependent, not random:
- A sync that finds **0 source records** writes nothing → logs
  `Synchronized 0 successfully` (no write, no permission check hit).
- A sync that finds records tries to **update/create** objects → denied →
  `Failed to synchronize: User 'Anonymous' does not have permission to 'update'`.

So in practice **no** scheduled sync currently persists data; only the empty ones
appear green.

## Key signal

A **manual, authenticated** run (e.g. `POST /api/synchronizations/{id}/run` with
admin auth, or the UI "Run job" as a logged-in admin) succeeds, because it runs
with that user's identity. Only the **scheduled** execution (cron, no session)
runs as `Anonymous`. So the fault is the job runner's execution identity, not the
synchronization config.

## Suggested fix (devs)

Run scheduled `SynchronizationAction` jobs with an authenticated **system /
service identity** that has write permission — e.g. execute as the job's owner
(`userId`) or a configured background-job user — instead of `Anonymous`. Options:
1. Honour the job's `userId` (currently null) as the execution identity, and let
   provisioning set it to a service account.
2. Run background jobs under a dedicated system user that bypasses/satisfies RBAC.
3. A setting for "job execution user" / "default object owner" applied to job runs.

## Possible provisioning workaround (needs dev confirmation)

The job's `userId` field **is settable** via `PUT /api/jobs/{id}` (confirmed on
canary: `{"userId": "admin"}` reflects). So if the runner honours `userId` as
the execution identity, the provisioner could set each job's `userId` to an
admin/service account (it already PATCHes job `arguments`). **Unverified**:
whether the scheduled runner actually uses `userId` (vs always Anonymous) needs
dev confirmation or a live cron-fire test. Until confirmed, the real fix is
dev-side.
