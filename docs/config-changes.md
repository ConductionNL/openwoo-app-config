---
last_reviewed: 2026-07-10
owner: info@conduction.nl
---

# Changing the config

The OpenWoo config is **not** edited live in a tenant and is **not**
imported from a hand-edited export. The single source of truth is
`config/woo.configuration.json` in this repo. Anyone changing the config
follows this flow — no exceptions:

1. `git switch -c feat/<what-changed>`
2. Make/obtain a fresh export → drop it in the repo as `raw-<date>.json`
   (git-ignored, never committed).
3. `make sanitize RAW=raw-<date>.json` — strips the postgres runtime
   pollution into `config/woo.configuration.json`.
4. `make lint && make test` — the gate. Fix any dangling refs / pollution.
5. `make functional` *(local)* — prove it imports into a clean Nextcloud
   (see [functional test](functional-test.md)).
6. Review the diff, open a PR. CI runs `lint` + `test` + secret scan.
7. Merge → tag a release. Nextcloud-base consumes the tag (see
   [design](design.md)).

This is the whole point of the repo: errors are caught here, in review
and CI, before the config can reach a tenant.

De gate uit stap 4 werkt — dit blok draait hem als geteste bewering
bij elke push (uitvoerbare documentatie):

```bash verify
make lint >/dev/null
```

## Make targets

```bash
make lint                 # CI gate: fail on runtime pollution / dangling refs
make sanitize             # strip pollution from config/woo.configuration.json in place
make sanitize RAW=fresh-export.json   # clean a fresh export into the canonical config
make test                 # run unit tests
make functional           # local layer-2: import into ephemeral Nextcloud (needs docker)
```

What the sanitizer strips and what the linter enforces is specified in
the [sanitizer & linter reference](sanitizer.md).
