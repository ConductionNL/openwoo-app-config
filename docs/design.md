---
last_reviewed: 2026-08-10
owner: info@conduction.nl
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

## Provisioning is operator-driven (public URL by default)

The target track is **operator-driven**: after `Nextcloud-base` deploys a
tenant, an operator runs `provision.py` (or the GUI) to converge the WOO
config and set the source connection. By default it drives the tenant's
**public URL** (a trusted domain), so it needs no standing in-cluster
wiring. We deliberately avoided standing Argo apps for this — the operator
runs it on demand.

**In-cluster mode (opt-in), for when the public record is unreliable.**
The public host is published by external-dns with a very short TTL and can
briefly flap while a tenant's ingress is (re)created; because nothing caches
a short-TTL record, a single run does one DNS lookup per step and any lookup
landing in an "absent" moment aborts the whole run with `[Errno -5] No
address associated with hostname`. In-cluster mode sidesteps this: it
connects to the tenant's **cluster-local Service**
(`http://nextcloud.<org>-<env>.svc.cluster.local:8080`, resolved over
`cluster.local` which never flaps) while presenting the public host via
`--host-header` — so Nextcloud still sees a trusted domain (the earlier
"internal Host isn't trusted" objection is exactly what `--host-header`
resolves). It also avoids a hairpin from the in-cluster GUI to its own
cluster's public ingress IP. Enable it with `provision.py --host-header`
or the GUI's "Via in-cluster service" checkbox (default on for
`*.commonground.nu`); it falls back to the public base for any other host.

## Hosted control-plane (`webgui/`)

The operator flow is also available as a small **hosted web GUI** — a
Flask app (`webgui/server.py`) that runs `provision.py all` from a form
and streams the log back. One hosted instance can converge any tenant —
over the tenant's public URL, or (default for `*.commonground.nu`) via the
cluster-local Service with `--host-header`, see the in-cluster note above.

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
- **PR-only, also for deletion.** The portal never mutates the cluster on
  the tenant path: it opens a PR on Nextcloud-base and stops. Every such
  PR is labelled `change/tenant-additive` (`TENANT_PR_LABEL`) because
  Nextcloud-base's `governance-check` fails any PR whose label does not
  match its classification. Labelling is best-effort: it happens after
  the PR exists, so a failure is logged and the PR is still returned.
- **What a merged delete-PR does — and does not.** The ApplicationSet
  removes the Applications (`nc-<tenant>`, `<tenant>-reactfront`), but
  `preserveResourcesOnDeletion: true` keeps the **resources**: the
  namespace, its PVCs and secrets, and the frontend Deployment with its
  Ingress, which keeps serving traffic. Removing that is a separate,
  deliberate step: `scripts/cleanup-tenant.sh --tenant <name>` (plan by
  default, `--execute` to act; production names are gated behind
  `--force-production`, tunable via `PROD_PATTERN` / `PROD_TENANTS`).
  DNS needs no action — external-dns runs `policy: sync` and drops the
  Cloudflare record once the Ingress is gone. The full removal procedure
  lives in Nextcloud-base `docs/REMOVING-TENANT.md`; it is not repeated
  here.

The host is named generically (`platform.`) because the control-plane is
intended to grow beyond provisioning (e.g. driving deployments) over time.

## The frontend image is three fields, not one

The tenant form writes `tenant.frontend.registry`, `.repository` and `.tag`
separately. The `react-tenants` ApplicationSet in `React-base` composes them
into `<registry>/<repository>:<tag>` and hands that to the chart as
`pwa.image.image` / `pwa.image.tag`. Leave all three blank and the frontend
follows the platform default in `react-platform/values/common.yaml`.

Three fields rather than one free-text image reference, because one field
invites a full reference — and a full reference in the tag position renders as
`docker.io/conduction2022/woo-website-v2:woo-website-v2:<tag>`, which is not a
valid image. That happened twice on 2026-08-11 (`epe-accept` and the newly
added `tubbergen-prod`): someone pasted a reference out of a registry UI into
the tag box and nothing rejected it.

Two things made it hard to see. The portal's `render()` writes the tag
verbatim, so there was no transformation to inspect. And the frontend image was
ignore-diffed in Argo CD, so the Application carried the broken value while the
live Deployment kept running the platform default and Argo reported
`Synced/Healthy`. The tenant file looked wrong, the cluster looked fine, and
nothing connected the two.

`validate()` now rejects a `/` or `:` in the tag, a tag in the repository, a
path in the registry, and a registry without a repository. The form carries
matching `pattern` attributes so the browser refuses it first, but the server
check is the gate — a `pattern` is a convenience, not a boundary.

`validate()` mirrors `validate-values.sh` in `Nextcloud-base`, and that
mirroring is a promise you have to maintain. It had lapsed: `validate-values.sh`
gained `tenant.frontend.*` checks that were missing here, so its CI only caught
the bad files *after* they merged to `main`. A new frontend check belongs on
both sides.

Not modelled: `themeClassname` is checked for shape (`<name>-theme`) but not
against the list of themes that actually exist. Those live in
`ConductionNL/conduction-theme` and are bundled into the image; the list changes
often and the image lags behind it, so a list check here would reject a theme
that was added yesterday. A wrong-but-well-formed theme still yields a site
without styling, silently.

## How Nextcloud-base consumes this

`Nextcloud-base` (the GitOps platform) does **not** own this config. It
consumes a tagged, validated version of `config/woo.configuration.json` —
the same way it pins app versions. Config errors are caught in *this*
repo's CI, before they can reach a tenant.
