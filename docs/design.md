---
last_reviewed: 2026-08-26
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

## The Nextcloud image is an exception, and it is guarded

The same three fields exist a second time, for the Nextcloud image itself:
a **top-level** `image:` block, not under `tenant:`, because it is a chart value
rather than a hub field. It works because the tenant file is the last entry in
the `nextcloud-tenants` ApplicationSet's `valueFiles`, so it wins over
`common.yaml`.

It is deliberately a separate, collapsed section in the form rather than sitting
next to the frontend fields. Almost every tenant should follow the platform
version; this is for one that needs a PHP extension the official image does not
ship — soap, for Woo BCT.

Adding the fields was the small half. The rule that makes them safe is one a
form cannot express by itself, from `Nextcloud-base/docs/ADDING-TENANT.md`:
**never point an existing tenant at a lower version**. `/var/www/html` is a PVC,
so the installed version survives a pod restart; the upstream entrypoint
compares it against the image and exits 1 when the image is older. With
`selfHeal: true` Argo retries forever, and recovery is reverting the tenant file,
not `kubectl`.

That rule was nearly broken twice in one month, both times through this portal's
blind spot:

- **2026-08-25** — asked whether tenants support a different image, the portal
  had nothing to answer from, and a reconstruction presented a 32.0.13 → 32.0.6
  downgrade as routine, citing `beek` as precedent. `beek` is a legacy
  standalone Application on chart 6.4.1, not a tenant of this ApplicationSet.
- **2026-08-26** — Nextcloud-base PR #100 re-added `harderwijk-prod`, removed
  one day earlier, pinned to `32.0.6-fpm-soap` while the file it replaced
  carried no override and therefore ran 32.0.13. It landed safely because the
  namespace happened to be cleaned up.

So the guard has three layers, with deliberately different hardness:

| Layer | Source | Hardness |
|---|---|---|
| git | effective current tag: the tenant file's, else `common.yaml`'s | blocks |
| argo | `status.summary.images` of `nc-<tenant>` | blocks; disagreement with git is a warning |
| history | did `tenant-<name>.yaml` exist and get removed? | warns only |

Git is authoritative for what should run; Argo reports what it sees. When they
disagree git wins the block decision, because a drifted cluster must not veto a
correct change.

The history layer is the PR #100 case, and it is exactly where the other two
know nothing: the file is gone and the Application has been pruned. Both
ApplicationSets set `preserveResourcesOnDeletion: true`, so the volume can still
be there. It warns rather than blocks — the portal may not read namespaces and
should not be given permission to, so it cannot know. Blocking on a maybe would
make re-adding any removed tenant impossible; staying silent is what let PR #100
through. The warning names the removed file's `dbType` and image, so a database
engine switch becomes visible too; that was the other thing nobody caught there.

A fresh tenant with no history may pin any version — there is no volume to
collide with. That distinction is why the guard reads the declaration and the
history rather than only comparing tags.

Two rules are enforced by *not* offering something. No `digest:` field: chart
8.9.0 does not render it, so git would claim a digest the podspec does not
carry. And no GHCR existence check: that would make form validation depend on an
external service, while a non-existent tag surfaces as `ImagePullBackOff` on
first sync — visible, not silent.

One thing the guard cannot see: it compares against the **podspec** image, not
the version actually installed in the PVC. Those normally agree; after a failed
upgrade they do not. Closing that gap needs `exec` in the tenant namespace, far
wider rights than `argolib`'s read-only-on-Applications. The history warning
covers the practical case.

### Version comparison is parsed, never string-compared

Lexically `"32.0.6-fpm-soap"` is **greater** than `"32.0.13-fpm"`, because
`'6' > '1'`. A string compare therefore concludes 32.0.6 is newer than 32.0.13
and lets the downgrade through — it says yes to exactly the case it exists to
catch. Hence `image_version()` / `compare_versions()` in `tenants.py`, and their
own tests. The build suffix (`-fpm`, `-fpm-soap`) is ignored: same version in a
different build is not a downgrade.

## Per-app version pins

`tenant.apps.versions` pins an app's version in git; leaving a key out means the
app tracks its latest release (the ApplicationSet then passes `""`). The form
offers one field per app, collapsed, blank by default — blank is the norm.

Exactly three apps are pinnable, because
`argo/applicationsets/nextcloud-tenants.yaml` maps exactly three keys to
`OPENCATALOGI_VERSION`, `OPENCONNECTOR_VERSION` and `OPENREGISTER_VERSION`.
`validate-values.sh` has **no** allowlist of app names, so a pin on a fourth name
passes its CI and then does nothing at all — a pin that never takes effect. In a
form that silent no-op is worse than an error, so `PINNABLE_APPS` is a closed set
here. Adding a fourth pinnable app means changing the ApplicationSet first.

The version format mirrors `validate_app_versions_format()` exactly, including
its separate message for a leading `v`. That message earns its place: GitHub
releases are named `v0.7.12` while the field wants `0.7.12`, so it is the mistake
people actually make. Three numeric parts are required — `0.7` is rejected.

### Why this had to land together with the image override

Making `image` a rendered key had a side effect that nearly caused silent data
loss. `unknown_keys()` never descended into `tenant.apps`, and `render()` did not
emit `apps.versions`. On `tenant-harderwijk-prod.yaml`, which carries both an
`image:` block and three pins, the unknown `image` key was the *only* thing
keeping the file read-only. Allowing `image` without rendering the pins would
have made the portal drop them on the next save.

So two things changed: `unknown_keys()` now descends into `apps`, and `versions`
is rendered. The descent still matters — anything else under `apps` that
`render()` does not know keeps a file read-only, which is the behaviour that
caught this in the first place.

## How Nextcloud-base consumes this

`Nextcloud-base` (the GitOps platform) does **not** own this config. It
consumes a tagged, validated version of `config/woo.configuration.json` —
the same way it pins app versions. Config errors are caught in *this*
repo's CI, before they can reach a tenant.
