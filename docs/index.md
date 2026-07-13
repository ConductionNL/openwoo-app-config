---
last_reviewed: 2026-07-06
owner: info@conduction.nl
---

# openwoo-app-config — documentation

Versioned, validated OpenRegister configuration for the OpenWoo app.
The config is version-controlled here and gated by CI before it is ever
loaded into a tenant. See the repo README for the quick overview.

## Pages

- [Design](design.md) — the problem this repo solves, the two tracks,
  operator-driven provisioning, the hosted control-plane, and how
  Nextcloud-base consumes the config (explanation).
- [Changing the config](config-changes.md) — the mandatory flow for every
  config change, plus the make targets (how-to).
- [Sanitizer & linter reference](sanitizer.md) — what gets stripped per
  bucket and which checks gate CI (reference).
- [Provisioning a tenant](provisioning.md) — running the target track
  against a tenant: env files, interactive prompts, GUI, source
  credentials (how-to).
- [Provisioner command reference](provisioner-commands.md) — every
  subcommand, what it does and what it asserts (reference).
- [Functional import test](functional-test.md) — the local layer-2 test
  against an ephemeral Nextcloud (how-to).

## Working notes (`notes/`)

Dated analysis records — kept for the audit trail, not maintained as
documentation:

- [PROVISIONING-TEST-PLAN](notes/PROVISIONING-TEST-PLAN.md)
- [BUG-import-forward-refs](notes/BUG-import-forward-refs.md)
- [BUG-sync-job-anonymous-permission](notes/BUG-sync-job-anonymous-permission.md)
