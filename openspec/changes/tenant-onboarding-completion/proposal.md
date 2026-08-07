## Why

`tenant-creation-pr-flow` gets a tenant *file* merged and Argo rolling it out with
no devops step. Three gaps remain before a PO can self-serve a full "custom
Nextcloud" end to end:

1. **Custom domain → cert.** `tenant.frontend.host` already lets an operator
   fill a custom hostname, and `frontend.tls` (`secretName` + `issuer`) has
   since landed as a live contract — written in Nextcloud-base tenant files,
   consumed by react-base's `react-tenants` ApplicationSet, including the
   `issuer: none` bring-your-own path. What is missing is (a) the webgui
   emitting those keys instead of leaving them out, and (b) a documented supply
   path for the cert *bytes* of a client-owned domain. Today someone still
   hand-carries a PEM/PFX, i.e. the devops dependency `tenant-creation-pr-flow`
   was built to remove.
2. **Theme.** Branding today is one string (`frontend.branding.organisationName`).
   There is no way to land the NL Design System Nextcloud theme (or a
   municipality's own logo/colors) on a tenant without an operator doing it by
   hand post-rollout.
3. **Initial password.** ESO generates `nextcloud-secrets` in-cluster (once
   task 1.3 of `tenant-creation-pr-flow` lands) — but nobody has told the
   product owner what it is except by a devops person reading the secret and
   messaging it, which is exactly the dependency this whole lifecycle is
   trying to remove.

## What Changes

- **Cert delivery — render the contract that already exists; deliver bytes out
  of band.** `frontend.tls` is not a new field: `react-tenants.yaml` reads
  `frontend.tls.secretName` (default `wildcard-openwoo-tls`) and
  `frontend.tls.issuer` (`none` ⇒ no cert-manager annotation, no Certificate
  object, no Let's Encrypt overwriting a customer cert). This change:
  - teaches `webgui/tenants.py` to render `frontend.tls.secretName` +
    `frontend.tls.issuer` when the operator supplies a custom `frontend.host`,
    following the naming already used in live tenant files (host-derived, e.g.
    `acceptatie-open-oude-ijsselstreek-nl-tls`) — no new naming convention,
  - documents the operator runbook for landing the actual cert into that Secret:
    `certswap`'s `k8s-secret` driver once it exists, `kubectl create secret tls`
    until then. Either way out of band from git and from the webgui — the PR
    only ever references the secret *name*, never its contents.
  This change does **not** add cert-upload code to the webgui.
- **Theme as a provisioner step, not a form field.** Theming (name, color,
  logo, optional NL Design System app) is tenant-*config*, not tenant-*shape*
  — it belongs next to the OpenRegister config convergence, not in the
  tenant-creation PR. Add `scripts/provisionlib/steps.py::theme` following
  the existing convergence pattern (idempotent, GET-checks first, `occ
  theming:config` equivalents over the Nextcloud OCS API), wired into
  `provision.py all` and callable standalone (`provision.py theme`).
- **One-time secret reveal — self-hosted, not a third-party SaaS.** A
  municipal admin password does not leave the cluster to a US SaaS
  (onetimesecret.com or similar) just to be shown once; that is a data-
  sovereignty regression for an ISO 27001 / Common Ground platform. Add a
  small stdlib-only burn-after-read mechanism to the webgui: `POST
  /tenant/<name>/secret-link` (operator-only, reads `nextcloud-secrets` via
  the existing in-cluster path, stores it **encrypted, single-read, TTL'd**
  under a random token) and `GET /reveal/<token>` (**no SSO required** — the
  PO doesn't have a Keycloak account — the token *is* the credential,
  displayed exactly once, then deleted).

## Capabilities

### New Capabilities

- `tenant-tls-custom-domain`: a tenant with a non-platform `frontend.host` gets
  its `frontend.tls` block written by the webgui (not hand-edited afterwards)
  and a documented, git-free path for the cert bytes via `certswap`'s
  `k8s-secret` driver, with a `kubectl` fallback.
- `tenant-theme-branding`: an operator (or later, the create-tenant form)
  can converge a tenant's Nextcloud theming (name/color/logo, optional NL
  Design System theme app) the same idempotent way OpenRegister config is
  converged today.
- `secret-reveal-once`: an operator generates a single-use, TTL'd link that
  shows the tenant's initial Nextcloud admin password exactly once, with no
  SSO dependency on the recipient and no persisted plaintext after the
  first read.

### Out of Scope

- `frontend-tls-contract`: defining and consuming `frontend.tls.secretName` /
  `issuer` is **already delivered** by react-base's `frontend-tls-and-migration`
  (`react-platform/argo/applicationsets/react-tenants.yaml`) and is live in
  Nextcloud-base tenant files on `main`. This change only makes the webgui
  render it.
- `automatic-acme-for-custom-domains`: DNS-01/HTTP-01 automation for
  arbitrary client-owned domains is a separate cert-manager/DNS project, not
  this change. This change only lands a BYO cert an operator already has.
- `byo-cert-renewal`: renewal of a hand-seeded customer cert is flagged as an
  open item in react-base's change and stays there.
- `certswap-implementation`: building/hardening `certswap` itself is tracked
  in its own repo/OpenSpec; this change only *consumes* its `k8s-secret`
  driver via a documented runbook.
- `nextcloud-provisioner-rename`: renaming `openwoo-provisioner` →
  `nextcloud-provisioner` touches ~20 manifests, the Keycloak client id, the
  image name, and the namespace — real work, explicitly **not** bundled here.
  Tracked as a follow-up (see design.md Open Questions).
- `eso-secret-wiring`: `secret-reveal-once` has a hard dependency on
  `tenant-creation-pr-flow` task 1.3 (ESO producing `nextcloud-secrets`).
  That wiring is not re-done here — if it isn't live, this change's reveal
  endpoint has nothing to read.
- `general-purpose-secret-sharing`: the reveal endpoint is purpose-built for
  exactly one secret (the Nextcloud admin password on tenant creation) — not
  a reusable "share any secret once" product.

## Impact

- **This repo (openwoo-app-config)**: `scripts/provisionlib/steps.py` gains a
  `theme` step; `webgui/tenants.py` renders the `frontend.tls` block;
  `webgui/server.py` gains `/tenant/<name>/secret-link` + `/reveal/<token>`;
  new stdlib-only burn-store module.
  Zero-third-party-dependency posture preserved.
- **certswap repo**: no code change required for this proposal to ship; its
  `k8s-secret` driver becomes a documented dependency of this platform's
  onboarding runbook. If `certswap` isn't built yet, the runbook's `kubectl
  create secret tls` fallback carries the flow.
- **Cluster**: the webgui's RBAC needs a narrow addition — read (not list)
  of one Secret (`nextcloud-secrets` in the tenant namespace) scoped to the
  reveal flow, and write of one ephemeral Secret/ConfigMap per reveal token.
  No new access to arbitrary secrets.
- **Nextcloud-base / react-base**: no change. `frontend.tls` is already a
  recognized optional field in the tenant file contract and already consumed by
  the `react-tenants` ApplicationSet; this change only stops the webgui from
  omitting it.
