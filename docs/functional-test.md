---
last_reviewed: 2026-07-06
owner: info@conduction.nl
---

# Layer 2 — functional import test (local)

Static `lint` cannot prove the config actually *loads*. `make functional`
does: it spins up an **ephemeral** Nextcloud + PostgreSQL
(`docker-compose.test.yml`), installs the Conduction apps, and imports
the config through the real OpenRegister API:

```
POST /apps/openregister/api/configurations/import   (multipart file=)
```

The stack is wiped after every run (`down -v`), so the test always proves
a **clean** tenant accepts the config — the same starting point a new
tenant has.

It then **verifies row counts** in PostgreSQL against the config:
registers, schemas, sources, mappings, rules and synchronizations must
all match. This is deliberate — the import API returns `HTTP 200 "Import
successful"` even when its response body omits the created rows (e.g. it
echoes `schemas: []` while 17 schemas were in fact written), so a
status-code check alone is not proof. The count check is.

After the row-count check, the test runs the **post-import provisioner**
(`scripts/provision.py`): for every source in the config it sets the
API-key header on the running instance and asserts it reflects — proving
the credential-provisioning path without performing any real data fetch.
In the test it writes a clearly-marked dummy key.

```bash
make functional                  # full cycle: up → install → import → assert → provision → teardown
KEEP_UP=1 make functional        # leave the stack at http://localhost:8080 to debug
```

> **Why not on Codeberg CI:** Codeberg's shared runners only provide
> buildah, not docker/compose, so this stack can't run there. Layer 2 is
> therefore a local check (and a candidate for a self-hosted nightly),
> while the static `lint`/`test` gate runs on every push. Auth uses the
> ephemeral container's own admin — **no live credentials live in
> Codeberg.**

Possible extension (not yet wired): round-trip — after import, `GET
.../configurations/{id}/export`, run it back through `sanitize`, and
assert the register/schema/source/sync slug-set matches the input. That
would also prove the sanitizer captures every runtime field OpenRegister
emits.
