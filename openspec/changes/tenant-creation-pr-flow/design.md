## Context

`webgui/server.py` is a small Flask control-plane that runs behind oauth2-proxy → Keycloak (which brokers Google). It already:
- derives the operator via `current_user()` from the proxy's `X-Forwarded-Email`,
- fails closed when `REQUIRE_AUTH` is on (every route but health needs an identity),
- is **read-only on this repo** and only mutates a *target tenant* via `provision.py` against that tenant's public URL,
- keeps a deliberate **zero-third-party-dependency** posture (stdlib only) for auditability.

This change adds the missing first step of the lifecycle — creating the tenant — without breaking any of those properties.

### External dependencies (must exist — not built here)

- A Codeberg/Forgejo bot account + scoped token for Nextcloud-base.
- Branch protection on Nextcloud-base `main` requiring PR review **for the bot** (no bot self-merge, no bot direct push). **Human admins (mwest2020) are exempt** — direct push stays available to them (Forgejo push-allowlist + review-bypass). The protection exists to gate machine-authored changes, not to slow the maintainer.
- **In-cluster secret generation via ESO** (decided 2026-06-22 — moving to External Secrets Operator regardless): a ClusterSecretStore + per-tenant ExternalSecret produces `nextcloud-secrets` for a new tenant, ideally auto-provisioned on namespace creation. The portal never generates or transmits secrets. (Wiring ESO is a separate Nextcloud-base/platform change — a hard prerequisite for end-to-end usefulness.)

## Goals / Non-Goals

**Goals**
- Operator creates a tenant from a form; output is a reviewable, bot-authored PR with the link returned to the form.
- The portal holds no secrets and gets no cluster write access for tenant creation.

**Non-Goals**
- Auto-merge / auto-deploy.
- Generating or transmitting tenant secrets through the portal.
- Forge-based SSO (optional, orthogonal).

## Decisions

### Decision 1: Extend the existing webgui, do not create a new repo

It is already the deployed, SSO-gated operator UI doing the config half. Adding tenant-creation here yields one coherent lifecycle UI and reuses the existing identity plumbing. A separate portal repo would duplicate auth, deploy, and CI for an MVP. Revisit only if the portal's concerns diverge enough to warrant a split.

### Decision 2: Portal is a PR-author, never a cluster/secret actor

The portal's only privileged capability is "open a PR on Nextcloud-base". This is the smallest blast radius that delivers "no devops":
- Tenant **file** → PR (this change).
- Tenant **secrets** → in-cluster (ESO/Job), never the portal.
- **Merge** → human review gate.

A compromised portal or leaked bot token can therefore only propose changes to one repo — it cannot deploy, read secrets, or write `main`.

### Decision 3: Talk to Forgejo over REST with stdlib `urllib`

Preserve the zero-dependency rule. The three calls needed are plain REST:
- create branch: `POST /repos/{owner}/{repo}/branches` (from `main`),
- write file: `POST /repos/{owner}/{repo}/contents/{path}` (base64 content, on the branch),
- open PR: `POST /repos/{owner}/{repo}/pulls` → response carries `number` + `html_url`.

`html_url` is returned straight to the form. No `git` binary, no clone, no PyGithub.

### Decision 4: Validate before proposing

Render the tenant YAML from a template and run the same checks Nextcloud-base CI runs (required fields, name/host convention) **before** opening the PR, so the portal never produces a PR that CI will fail. The Nextcloud-base validator is the contract; mirror its rules (or shell to a vendored copy) rather than re-deriving them.

### Decision 5: Provenance via commit trailer + PR body

The git-write identity authors the commit; the human requester is recorded as `requested-by: <email>` (commit trailer + PR body), and the PR is labelled machine-authored. This keeps "who asked" auditable even though the git identity is the token-holder, not the clicking operator.

### Decision 6: MVP uses the maintainer's scoped token; dedicated bot is the hardening follow-up

For the MVP the git-write identity is a `write:repository`-scoped personal access token on the maintainer's account (MWest2020), not a separate bot. Rationale: Codeberg/Forgejo has no email-invite — a collaborator must be an existing account — so a dedicated bot needs its own registered account first; that is deferred.

**Caveat (recorded deliberately):** Forgejo PATs are scoped by *capability*, not by repository. A `write:repository` token on a human account can write to **every repo that account can reach**, so the MVP's blast radius is all of MWest2020's repos, not just Nextcloud-base. Mitigations: least capability (`write:repository` only, no admin/org scopes), token stored only as a k8s Secret (never git, never logged), and the planned migration to a dedicated `openwoo-bot` collaborator-on-one-repo as hardening. Until then, PRs are authored as MWest2020 with the operator stamped via `requested-by:`.

## Risks / Trade-offs

- **Secret path is a hard dependency.** Without automated in-cluster secret generation, a portal-created tenant deploys without `nextcloud-secrets` and fails. This change is only *useful* once that path exists; sequence accordingly.
- **Bot token custody.** A write-scoped token is sensitive; mitigated by least-scope (PR only, one repo), k8s Secret storage, branch protection (no self-merge), and revocability.
- **Form ≠ full tenant expressiveness.** The form should cover the common case (name/env/db/apps/frontend); rare edge fields (hostnameOverride, vng-style non-standard apps) may still need a hand-edited PR. Keep the form to the 80% and allow "advanced YAML" passthrough later.
- **Duplicate-name / race.** Two operators creating the same tenant → branch/file collision. Handle the Forgejo 409/422 gracefully and surface it in the form.

## Migration / Rollout Plan

1. Ship behind the existing auth, gated by a feature flag / separate route; config provisioning unaffected.
2. Dry-run against a throwaway tenant (like `conduction-straattest-accept`) — confirm the PR opens, validates, and the link returns.
3. Only enable for operators once the in-cluster secret path is confirmed, so created tenants actually come up.

## Open Questions

- **ESO wiring** (mechanism decided — ESO; details open): ClusterSecretStore backend + whether the per-tenant ExternalSecret is auto-created on namespace creation or templated alongside the tenant file. Separate Nextcloud-base/platform change; hard prerequisite for end-to-end usefulness.
- **Bot account**: reuse an existing machine account or mint `openwoo-bot`? Token scope exact name in Forgejo.
- **Merge automation later**: keep manual forever, or allow auto-merge for low-risk accept tenants once trust is established? (Default: manual.)
- **Form scope**: which fields are first-class vs an "advanced YAML" escape hatch?
