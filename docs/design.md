---
last_reviewed: 2026-07-06
owner: mark
---

# Design — why this repo works the way it does

## The problem this solves

The OpenWoo config is an OpenRegister *configuration export* — an
OpenAPI-enveloped JSON document (`openapi` / `info` / `components` /
`x-openregister`) whose `components` hold the config buckets: `registers`,
`schemas`, `mappings`, `sources`, `rules`, `synchronizations`, `endpoints`,
`jobs`, `workflows`, `objects`. It is ~7,500 lines pretty-printed, devs
hand-edit and re-export it, and mistakes in it silently break app
functionality.

When an export is taken from an instance that has **imported into
PostgreSQL**, runtime state (sync cursors, content hashes, last-synced
timestamps, created/updated metadata) leaks back into the export and
pollutes the config. That noise causes broken diffs and unpredictable
imports. This repo version-controls the config and **gates it in CI**
before it can ever reach a tenant.

Zero third-party dependencies is deliberate: full auditability, no
supply-chain surface, reproducible anywhere `python3` exists.

## Two tracks: source validation and target configuration

| Track | Question it answers | Tooling | Needs a tenant? |
|-------|---------------------|---------|-----------------|
| **Source** — config validation | Is the config artefact correct and portable? | `scripts/oac.py` (`lint` / `sanitize`) + `tests/` | no — runs on the file |
| **Target** — configuration, validation & repair | Is a running tenant in the desired state, and bring it there | `scripts/provision.py` | yes — points at a tenant URL |

The **source track** is the CI gate: pollution, dangling refs and bad
authorization keys are caught on the JSON before it can reach a tenant.
The **target track** drives a real tenant over the API and asserts each
step — some steps *validate* (`verify-import`, `sync-check`), others
*configure or repair* (`settings`, `oc-settings`, `import`,
`authorization`, `catalog`, `delete-menu`, `credentials`).

## Provisioning is operator-driven (not in-cluster)

The target track runs from **outside** the cluster, against a tenant's
**public URL** (a trusted domain), so it needs no in-cluster wiring:
after `Nextcloud-base` deploys a tenant, an operator runs `provision.py`
(or the GUI) to converge the WOO config and set the source connection.
Driving tenants in-cluster was tried and dropped — the internal service
Host isn't a trusted domain, and it added standing Argo apps nobody
wanted. Outbound-from-outside is simpler and works.

## Hosted control-plane (`webgui/`)

The operator flow is also available as a small **hosted web GUI** — a
Flask app (`webgui/server.py`) that runs `provision.py all` from a form
and streams the log back. It drives tenants **outbound** over their
public URLs, so one hosted instance can converge any tenant.

- **Auth:** no login of its own — it sits behind **oauth2-proxy →
  Keycloak** (realm `commonground`), which brokers **Google**. The app
  **fails closed** (`REQUIRE_AUTH`, default on): every route except
  `/healthz` returns `403` without the proxy's identity header. See
  `webgui/auth/README.md`.
- **Creds model A:** the operator types the tenant password + source key
  per run; nothing is stored. Secrets go to the subprocess via env,
  never argv, never logs.
- **Deploy:** `webgui/deploy/` (kustomize) — app on `127.0.0.1` +
  oauth2-proxy sidecar as the sole listener, NetworkPolicy, nginx
  Ingress + TLS, at `platform.commonground.nu`. Build the image with
  `make image`. See `webgui/deploy/README.md`. Local dev:
  `REQUIRE_AUTH=false python3 webgui/server.py`.

The host is named generically (`platform.`) because the control-plane is
intended to grow beyond provisioning (e.g. driving deployments) over time.

## How Nextcloud-base consumes this

`Nextcloud-base` (the GitOps platform) does **not** own this config. It
consumes a tagged, validated version of `config/woo.configuration.json` —
the same way it pins app versions. Config errors are caught in *this*
repo's CI, before they can reach a tenant.
