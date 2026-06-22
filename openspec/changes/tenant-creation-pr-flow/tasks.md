## 1. Prerequisites — confirm before building

- [ ] 1.1 Enable branch protection on Nextcloud-base `main` requiring PR review **for the bot** (no bot self-merge / no bot direct push), with **mwest2020/admins exempt** (Forgejo push-allowlist + review-bypass — the maintainer keeps direct push). Confirm the protection does not block existing maintainer/CI flows.
- [x] 1.2 **MVP decided (2026-06-22): use a `write:repository`-scoped PAT on the maintainer's account (MWest2020)** — no separate bot for now (Forgejo has no email-invite; a dedicated bot needs its own registered account, deferred). Token stored only as a k8s Secret (never git/logged); shape documented in `webgui/deploy/secret.example.yaml`. Caveat: Forgejo PATs are capability-scoped, not repo-scoped → blast radius is all of MWest2020's repos. Hardening follow-up: migrate to `openwoo-bot` as collaborator-on-Nextcloud-base only.
- [ ] 1.3 Wire the ESO secret path (mechanism decided — External Secrets Operator): ClusterSecretStore + per-tenant ExternalSecret producing `nextcloud-secrets`, ideally auto-provisioned on namespace creation. Separate Nextcloud-base/platform change; gates end-to-end usefulness, not the PR code.

## 2. Stdlib Forgejo client — unit-tested, no network in tests

- [ ] 2.1 `webgui/gitlib/` (stdlib `urllib` only): `create_branch(base, name)`, `put_file(branch, path, content, message)`, `open_pr(head, base, title, body)` → returns `{number, html_url}`.
- [ ] 2.2 Unit tests with a mocked HTTP layer covering: happy path, 409/422 (duplicate branch/file), auth failure, network error. Keep zero third-party deps (mirror the repo's stdlib-only test style).

## 3. Tenant rendering + validation

- [ ] 3.1 Template `tenant-<name>.yaml` from form values (name, environment, dbType, apps, optional `frontend` incl. `tls`). Reuse the Nextcloud-base tenant template shape.
- [ ] 3.2 Pre-PR validation mirroring Nextcloud-base `validate-values.sh` (name/env suffix rule, host convention, required fields). Reject in the form before any PR is opened.

## 4. Web route + form

- [ ] 4.1 `POST /tenant` route, auth-gated like `/provision` (`_require_operator`), stamping `requested-by` from `current_user()`.
- [ ] 4.2 Form template + result view that renders the PR link (`html_url`) on success and surfaces validation / 409 errors clearly.
- [ ] 4.3 Commit trailer + PR body include `requested-by: <email>`; PR labelled machine-authored.

## 5. Deploy + docs

- [ ] 5.1 Wire the bot-token Secret into `webgui/deploy/` (Deployment env/volume); keep oauth2-proxy as sole ingress.
- [ ] 5.2 `webgui/README.md`: document the create-tenant flow, the bot-token setup, and the "secrets stay in-cluster / portal opens PRs only" boundary.
- [ ] 5.3 `CHANGELOG.md` (this repo) updated.

## 6. Dry-run + done criteria

- [ ] 6.1 Behind a feature flag, create a throwaway tenant (e.g. a `conduction-*` test name) end-to-end: form → validated YAML → bot PR → link returned. Verify the PR passes Nextcloud-base CI.
- [ ] 6.2 Do NOT enable for operators until 1.3 (secret path) is live, so created tenants actually come up.
- [ ] 6.3 Done: an authenticated operator creates a tenant from the form and receives a working PR link; merge + Argo + in-cluster secret generation bring the tenant up with no manual devops step.
