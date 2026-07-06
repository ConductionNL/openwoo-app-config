---
last_reviewed: 2026-07-06
owner: mark
---

# Provisioner command reference

A real tenant bring-up is more than the import. `provision.py` performs
the post-install steps the config owns, over the API, each asserting it
took effect ("test what you ship"). Every step is one subcommand with its
own unit tests. The logic lives in the `provisionlib` package
(`constants` / `helpers` / `client` / `steps` / `cli`); `provision.py` is
a thin entrypoint, and callers can also `import provisionlib as
provision` to reuse the steps as a library.

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
| `delete-menu` | delete the OpenCatalogi default `User Menu` object (not part of the WOO config) | GET no longer lists it (idempotent — skips when absent) |
| `all` | run the bring-up in order, gating each step | settings → verify-import → credentials → sync-check → (`--run-syncs`) |

`verify-import` and `sync-check` exist because the import API returns
HTTP 200 even when it silently drops rows: on a tenant that already holds
data the bulk row count can't see the gap, but a slug-level diff can.
(They caught exactly this on canary — see
[notes/PROVISIONING-TEST-PLAN.md](notes/PROVISIONING-TEST-PLAN.md).)

Connection flags are shared: `--base`, `--user`, and `--password` /
`--password-env` (the env form keeps the secret out of argv). Steps that
read the config also take `--config`.
