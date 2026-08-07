## Context

`tenant-creation-pr-flow` shipped the "create a tenant with no devops step"
skeleton: form → validated YAML → bot/PAT-authored PR → human merge → Argo.
Three loose ends stop that from being a complete PO-facing lifecycle, and
this change closes them one at a time, each as small a blast radius as the
capability allows — matching the existing security posture (portal never
holds secrets it didn't strictly need to touch; stdlib only; every write is
auditable).

### What already exists (verified 2026-08-07, do not rebuild)

- `frontend.tls.secretName` and `frontend.tls.issuer` are a **live contract**.
  `react-platform/argo/applicationsets/react-tenants.yaml` (react-base, on
  `main`) defaults `secretName` to `wildcard-openwoo-tls` and treats
  `issuer: none` as "emit no `cert-manager.io/cluster-issuer` annotation", so a
  pre-seeded customer cert is never overwritten by Let's Encrypt.
- Nextcloud-base tenant files on `main` already carry the block, with
  host-derived secret names — e.g. `tenant-oudeijsselstreek-accept.yaml`:
  `secretName: acceptatie-open-oude-ijsselstreek-nl-tls`.
- Only `webgui/tenants.py` is behind: task 3.1 of `tenant-creation-pr-flow`
  deferred `frontend.tls` to "the react-base change's contract". That contract
  has landed; the webgui has not caught up.

### External dependencies (must exist — not built here)

- **ESO** producing `nextcloud-secrets` per tenant namespace
  (`tenant-creation-pr-flow` task 1.3, still open) — gates `secret-reveal-once`.
- **`certswap`**, an external CLI, for the ergonomic cert-import path.
  Verified 2026-08-07: working code at v0.3.0+. Not required to ship — the
  runbook's `kubectl create secret tls` fallback works today.

## Goals / Non-Goals

**Goals**
- A custom-domain tenant gets a working cert without hand-editing the tenant
  file after the PR and without hand-carrying a file outside any tooling.
- A tenant's Nextcloud theming converges the same way its OpenRegister
  config does — idempotent, re-runnable, no manual `occ` commands.
- A PO can retrieve the initial admin password once, without a devops
  person reading a Secret and pasting it into chat/email.

**Non-Goals**
- Solving ACME for arbitrary customer-owned domains.
- Renewal of bring-your-own certs (react-base's open item).
- A general-purpose "share any secret" tool.
- The `openwoo-provisioner` → `nextcloud-provisioner` rename.

## Decisions

### Decision 1: Cert *bytes* are `certswap`'s job, not the webgui's

The webgui's entire security story rests on "it never holds secrets, it
only opens PRs or touches one narrowly-scoped thing." Accepting a pasted
PEM+key over an HTTP form and writing it to a cluster Secret would be a new,
much larger privileged surface (arbitrary Secret write) for a feature that
is needed rarely (custom domains are the exception, not the rule).

`certswap` already does exactly this: polymorphic ingest (PFX, PEM, separate
files, PKCS#7, archives) normalized to one bundle, with a Kubernetes target
that swaps a `kubernetes.io/tls` Secret in place and is ArgoCD-aware, plus an
evidence trail per swap. That's a purpose-built tool with its own (small)
blast radius: it needs write access to one Secret, run by an operator from
their own machine or a CI job, not embedded in a customer-facing web app.

**Decision:** the webgui only records *that* a tenant expects a custom cert
(the `frontend.tls` block) in the tenant file. The actual bytes travel
out of band, run by an operator, as a runbook step between "PR merged" and
"tenant reachable on its custom domain":
`certswap plan k8s <bundle> --namespace <ns> --secret <name>` to preview, then
`apply` with `--argocd-app nc-<tenant>` so the swap does not fight Argo. The
fallback everyone can run without installing anything is `kubectl create
secret tls`. No new code in this repo does cert I/O.

### Decision 2: Theme is a provisioner step, reusing the convergence pattern

`provision.py all` already treats every OpenRegister object as
"GET-check, write only if drifted." Nextcloud theming (name, slogan, color,
logo) is the same shape of problem — a handful of `occ theming:config`-
equivalent settings exposed over the OCS API, safe to re-apply. Making it a
new `steps.py::theme` function keeps one mental model for "converge a
tenant" instead of a second, form-driven path with its own idempotency
rules.

The **NL Design System** theme (`ConductionNL/nldesign-theme-nextcloud`) is
a Nextcloud *app*, not a config value — enabling/disabling it is a step of
its own (`occ app:enable` equivalent), gated the same way
`opencatalogi`/`openconnector`/`openregister` already are in
`webgui/tenants.py::KNOWN_APPS`. This change adds it as a known, optional
app; it does not change how apps are enabled.

**Rejected alternative:** doing theming in the tenant-creation form (like
`frontend.branding.organisationName`). Rejected because the form's contract
is "shape of the tenant at creation time," and theme changes happen after
creation too (rebrand, add NL Design System later) — provisioner steps are
already re-runnable against a live tenant; the form is not.

### Decision 3: Self-hosted burn-after-read, never a third-party SaaS

Sending a municipal Nextcloud admin password to onetimesecret.com (or any
external SaaS) to "show it once" means the plaintext transits a US-based
third party outside the ISO 27001 boundary, for a platform whose entire
design philosophy elsewhere is "boring, auditable, zero third-party
dependency." That is inconsistent with every other decision in this repo
and is not a hard problem worth outsourcing:

- generate a random 256-bit token,
- store `{ciphertext, expires_at}` under `sha256(token)` (never the raw
  token) — a Secret or ConfigMap in the webgui's own namespace is enough at
  this scale, no new datastore,
- `GET /reveal/<token>`: look up, decrypt, **delete**, return. Second
  request → 404. Expiry (default 24h, env-tunable) deletes it regardless of
  whether it was read.
- this route is **deliberately outside** `_require_operator` — the PO has
  no Keycloak identity. The unguessable token *is* the auth. Rate-limit by
  IP to blunt brute-force (256-bit space makes this a formality, not a real
  exposure).
- the generating route (`POST /tenant/<name>/secret-link`) **is**
  operator-gated — only an authenticated operator can mint a link, same as
  every other mutating route in this webgui.

**Rejected alternative:** self-hosting the open-source Onetime Secret
project as a sidecar. Rejected for now — it's a Ruby app, a new deploy
surface, a new datastore (Redis), for a feature whose actual requirement
("show this one string once") is ~60 lines of stdlib Python. Revisit if the
requirement grows (multiple secret types, longer TTL policies, audit UI).

### Decision 4: The TLS secret name follows the live convention, not a new one

An earlier draft of this change proposed a derived, fixed name
`nc-<tenant>-tls-custom`. Rejected on two counts:

1. **It would be a third convention.** The appset default is
   `wildcard-openwoo-tls`; live custom-domain tenants use a host-derived name
   (`acceptatie-open-oude-ijsselstreek-nl-tls`). Adding a third naming scheme
   makes the fleet harder to audit, not easier.
2. **`nc-` names the wrong object.** `nc-<tenant>` is the Argo application
   prefix for the Nextcloud backend; the cert in question terminates the
   **react frontend** on the customer domain. The prefix would mislead.

**Decision:** `webgui/tenants.py` derives the secret name from
`frontend.host` the same way the live tenant files do (host, dots and slashes
→ dashes, suffix `-tls`), and defaults `issuer` to `none` for a custom domain
(bring-your-own) unless the operator explicitly picks a cluster-issuer. The
derivation lives in one function with unit tests, so the fleet keeps exactly
one machine-checkable rule.

## Risks / Trade-offs

- **`certswap` is an external tool, not a platform component.** It exists and
  works (v0.3.0+, verified 2026-08-07), but it is not maintained inside this
  organisation, so a production runbook must not depend on it exclusively —
  that would put a customer-facing onboarding step behind a third party's
  release cadence and availability. Mitigation, and the reason this is a
  trade-off rather than a blocker: the runbook documents `kubectl create secret
  tls` as a fully sufficient first-class path, with `certswap` as the
  ergonomic option. Anyone can complete the onboarding without installing it.
- **`issuer: none` means no auto-renewal.** A hand-seeded customer cert
  expires silently. The runbook must record the expiry date and the owner;
  monitoring already has `CertificateExpiringSoon`, which should be confirmed
  to cover a Secret with no backing `Certificate` object.
- **Reveal endpoint is unauthenticated by design.** The token is the only
  gate. Mitigate: 256-bit token, short default TTL, delete-on-read,
  rate-limit, and log *that* a reveal happened (operator, tenant, timestamp)
  without ever logging the secret value itself.
- **Theme step touches a live tenant**, same trust boundary as existing
  OpenRegister provisioning — no new risk class, but worth stating: it's
  operator-run against a tenant's public URL, same as `provision.py` today.
  `docs/agents.md` classifies running `provision.py` against a tenant as
  **mens-vereist**; the theme step inherits that.
- **ESO dependency (task 1.3 of `tenant-creation-pr-flow`) is still open**
  (confirmed unchecked on `main`, 2026-08-07). If it isn't live,
  `secret-reveal-once` has nothing to read — don't enable the reveal route for
  operators until it is (same gating pattern the prior change already used for
  the create-tenant form).

## Migration / Rollout Plan

1. Ship `theme` as a provisioner step first — no dependency on anything
   else in this change, immediately useful standalone.
2. Ship the `frontend.tls` rendering in `tenants.py` + the runbook. Usable
   immediately (the consuming contract is already live); `certswap` only makes
   step 3 of the runbook nicer.
3. Ship `secret-reveal-once` behind a feature flag; do not enable for
   operators until ESO (`nextcloud-secrets`) is confirmed live end-to-end.
4. Dry-run each capability against a throwaway tenant before enabling
   broadly, consistent with `tenant-creation-pr-flow`'s rollout plan.

## Open Questions

- **Reveal link transport**: does the operator paste the `/reveal/<token>`
  URL into an existing email/Slack channel to the PO, or does this change
  also need a "send" step? Default: operator copies the link and sends it
  through whatever channel they already use — this change only mints the
  link.
- **TTL policy**: 24h default proposed; confirm against how PO onboarding
  actually happens (same day vs. next business day). Env-tunable either way.
- **Issuer default for a custom domain**: `none` (BYO) or `letsencrypt-prod`
  (HTTP-01 on the customer domain)? Live tenants show both. Proposal: default
  `none` and make the operator opt in to an issuer, because the failure mode of
  a wrong issuer (LE overwrites a paid cert) is worse than the failure mode of
  a missing one (Ingress serves the wildcard until the Secret is seeded).
- **`nextcloud-provisioner` rename**: worth doing before or after this
  change ships, given ~20 manifests + Keycloak client id + image name all
  reference `openwoo-provisioner`? Recommend *after* — renaming mid-flight
  on top of new routes/RBAC just adds review noise to both.
- **NL Design System theme app id**: confirm the actual Nextcloud app id
  shipped by `ConductionNL/nldesign-theme-nextcloud` before wiring
  `steps.py::theme`'s app-enable call.
