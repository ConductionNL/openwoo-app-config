## 1. Prerequisites — confirm before building

- [ ] 1.1 Enable branch protection on Nextcloud-base `main` requiring PR review **for the bot** (no bot self-merge / no bot direct push), with **mwest2020/admins exempt** (Forgejo push-allowlist + review-bypass — the maintainer keeps direct push). Confirm the protection does not block existing maintainer/CI flows.
- [x] 1.2 **MVP decided (2026-06-22): use a `write:repository`-scoped PAT on the maintainer's account (MWest2020)** — no separate bot for now (Forgejo has no email-invite; a dedicated bot needs its own registered account, deferred). Token stored only as a k8s Secret (never git/logged); shape documented in `webgui/deploy/secret.example.yaml`. Caveat: Forgejo PATs are capability-scoped, not repo-scoped → blast radius is all of MWest2020's repos. Hardening follow-up: migrate to `openwoo-bot` as collaborator-on-Nextcloud-base only.
- [ ] 1.3 Wire the ESO secret path (mechanism decided — External Secrets Operator): ClusterSecretStore + per-tenant ExternalSecret producing `nextcloud-secrets`, ideally auto-provisioned on namespace creation. Separate Nextcloud-base/platform change; gates end-to-end usefulness, not the PR code.

## 2. Stdlib Forgejo client — unit-tested, no network in tests

- [x] 2.1 `webgui/gitlib.py` (stdlib `urllib` only): `create_branch`, `put_file`, `open_pr` → `{number, html_url}`, plus `propose_file` orchestrating the three. Config from env; `GitlibError` with `.status` for clean route mapping.
- [x] 2.2 `tests/test_gitlib.py`: mocked `urlopen` covering happy path, 409 (duplicate branch), URLError (network), missing config, and a token-never-leaks-into-error assertion. (422 maps the same as other 4xx → 400.)

## 3. Tenant rendering + validation

- [x] 3.1 `webgui/tenants.py`: render `tenant-<name>.yaml` from form values (name, environment, wave, dbType, apps, optional `frontend.host` / `frontend.branding.organisationName`). Stdlib only — emitted as text, no PyYAML. (`frontend.tls` deferred to the react-base change's contract.)
- [x] 3.2 `tenants.validate()` mirrors `validate-values.sh` (name `<org>-<accept|test|demo|prod>`, env-matches-suffix incl. test/demo→accept, dbType enum, apps non-empty/known). `tests/test_tenants.py` covers each rule.

## 4. Web route + form

- [x] 4.1 `POST /tenant` route, auth-gated by the existing `_require_operator`, stamping `requested-by` from `current_user()`.
- [x] 4.2 `templates/tenant.html` form + result view rendering the PR link on 201 and surfacing validation / 409 errors. `GET /tenant` serves the form.
- [x] 4.3 Commit message + PR body include `requested-by: <email>`; PR body marks it machine-authored. Covered by `tests/test_webgui.py` (`/tenant` happy/validation/conflict/requester-stamp).

## 5. Deploy + docs

- [ ] 5.1 Wire the git-token Secret into `webgui/deploy/` Deployment env (`FORGEJO_TOKEN` from `openwoo-provisioner-git`, `FORGEJO_API_URL`/`TENANTS_REPO`/`TENANTS_BASE` as plain env); keep oauth2-proxy as sole ingress. Secret shape documented in `secret.example.yaml`.
- [ ] 5.2 `webgui/README.md`: document the create-tenant flow, the bot-token setup, and the "secrets stay in-cluster / portal opens PRs only" boundary.
- [ ] 5.3 `CHANGELOG.md` (this repo) updated.

## 6. Dry-run + done criteria

- [ ] 6.1 Behind a feature flag, create a throwaway tenant (e.g. a `conduction-*` test name) end-to-end: form → validated YAML → bot PR → link returned. Verify the PR passes Nextcloud-base CI.
- [ ] 6.2 Do NOT enable for operators until 1.3 (secret path) is live, so created tenants actually come up.
- [ ] 6.3 Done: an authenticated operator creates a tenant from the form and receives a working PR link; merge + Argo + in-cluster secret generation bring the tenant up with no manual devops step.
