## Why

Today a new WOO environment still needs a devops person: someone copies a tenant template into Nextcloud-base `values/tenants/`, runs the secret script, commits and pushes. The north star ("Argo ís de watcher") is that the Nextcloud-base tenant file is the single source of truth and a UI form writes it — so PO/management can create a tenant without touching git or the cluster.

The webgui in this repo is already the SSO-gated operator control-plane (platform.commonground.nu): it sits behind oauth2-proxy → Keycloak, derives the operator from `current_user()`, and already provisions OpenRegister config into an existing tenant via `provision.py`. It is the natural place to add the *first* half of the lifecycle — creating the tenant itself — so one UI covers "create tenant → (merge + Argo) → provision config".

The boring, auditable way to "create a tenant without a devops person" is **not** to give the portal cluster access or the operator's git credentials. It is:

- The portal opens a **pull request** on Nextcloud-base as a dedicated **bot** identity. The merge stays a human review gate — so `main` is never written directly and every tenant is reviewable.
- Authentication of the human (oauth2-proxy/Keycloak) answers *who asked*; it is deliberately decoupled from the git-write identity (the bot). The operator's brokered login is **not** used as a git token.
- Secrets never enter the portal: tenant secrets are generated **in-cluster** (ESO / password-gen Job), keyed off the new tenant. The portal's entire blast radius is "can open a PR on one git repo".

## What Changes

- **`webgui/server.py`**: add a `POST /tenant` route + a form (`templates/`) that collects the minimal tenant fields (name, environment, dbType, apps, optional `frontend` block) and:
  1. renders `values/tenants/tenant-<name>.yaml` from a template,
  2. validates it locally (reuse Nextcloud-base `validate-values.sh` semantics),
  3. creates a branch + commits the file + opens a PR on Nextcloud-base **via the Forgejo API using stdlib `urllib`** (zero new dependencies),
  4. returns the PR `html_url` to the form (the requested "link back to the PR").
- **`webgui/gitlib/` (new, stdlib only)**: a thin Forgejo API client — create branch from `main`, put file contents, open PR. No PyGithub / no `git` binary required (uses the contents + pulls REST endpoints).
- **Bot identity**: a dedicated Codeberg machine user (e.g. `openwoo-bot`) with a token scoped to repository write / PR only on Nextcloud-base, delivered as a k8s Secret to the webgui Deployment (`webgui/deploy/secret.example.yaml` documents the shape; real value out-of-band).
- **Provenance**: PR body + commit trailer carry `requested-by: <oauth2-proxy email>`; PR title/labels mark it machine-authored.
- **Docs**: `webgui/README.md` + this change document the create-tenant flow, the bot token setup, and the secrets-stay-in-cluster boundary.

## Capabilities

### New Capabilities

- `tenant-create-via-form`: an authenticated operator creates a tenant from a form; the result is a reviewable PR on Nextcloud-base, not a direct cluster or `main` write.
- `pr-link-back`: the form shows the opened PR's URL (and can later poll merge status) so the operator can follow the change to merge and Argo rollout.
- `bot-authored-prs`: all portal-created tenant changes are authored by a scoped bot with human-requester provenance — auditable, revocable, merge-gated.

### Out of Scope

- `secrets`: the portal never creates or holds tenant secrets. In-cluster generation **via ESO** (decided 2026-06-22) is a **dependency**, not part of this change (see Open Questions).
- `break-current-pipelines`: the existing config-provisioning route and current CI stay untouched; the new tenant-creation flow ships behind a feature flag so nothing live regresses.
- `direct-merge` / `auto-deploy`: no auto-merge. A human reviews and merges; Argo then rolls out per existing governance.
- `frontend-tls`: the `tenant.frontend.tls` contract is the separate react-base `frontend-tls-and-migration` change; this portal merely lets an operator *fill* those fields.
- `forge-login-idp`: adding GitHub/Codeberg as a Keycloak Identity Provider is optional and orthogonal — the existing Google login already authenticates operators. Not required for this change.

## Impact

- **This repo (openwoo-app-config)**: new `openspec/` scaffold; new webgui route + stdlib git client + form; new deploy Secret for the bot token. Zero-dependency posture preserved.
- **Nextcloud-base**: receives bot-authored PRs adding `values/tenants/tenant-*.yaml`. No change to its CI, which already validates tenant files (the PR is gated by the same checks). Branch protection on `main` must require review (confirm it does).
- **Keycloak**: no change required. (Forge IdP is optional and out of scope.)
- **Cluster**: no new portal permissions. The portal talks only to the Forgejo API over the network; it does not get kube credentials for tenant creation.
- **Security boundary**: a leaked portal/bot token can open PRs on one repo — it cannot merge, cannot read secrets, cannot touch the cluster. Token is scoped and revocable.
