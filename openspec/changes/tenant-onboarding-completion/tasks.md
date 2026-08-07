## 1. Prerequisites — confirm before building

- [ ] 1.1 Confirm `tenant-creation-pr-flow` task 1.3 (ESO → `nextcloud-secrets`)
      status — hard dependency for section 4 (`secret-reveal-once`). Not a
      dependency for sections 2 (theme) or 3 (TLS rendering).
      **Status 2026-08-07: still unchecked on `main`.**
- [x] 1.2 Confirm whether `certswap` has any working code yet — **done
      2026-08-07: yes.** Working CLI at v0.3.0+ with a Kubernetes target
      (`certswap plan|apply k8s <bundle> --namespace <ns> --secret <name>`),
      polymorphic ingest (PFX, PEM, separate files, PKCS#7, archives), an
      ArgoCD-aware in-place secret swap, and an evidence trail per swap.
      Caveat for section 3: it is an **external, non-ConductionNL tool** — the
      runbook keeps `kubectl create secret tls` as a first-class fallback so
      the platform is never blocked on it (see design.md Risks).
- [ ] 1.3 Confirm the NL Design System Nextcloud app id
      (`ConductionNL/nldesign-theme-nextcloud`) and its OCS/`occ` enable path.

## 2. Theme provisioner step

- [x] 2.1 `scripts/provisionlib/steps.py::provision_theme`: GET current theming
      via the OCS app-config API, diff against desired, write only what's
      drifted, re-GET and assert. Idempotent, same pattern as existing
      OpenRegister steps. **Scope correction:** logo/background/favicon are file
      uploads to the theming app's session+CSRF ajax route, not app-config
      values, so they are not settable over the basic-auth API the provisioner
      uses — documented as a gap, not silently dropped.
- [x] 2.2 `provision_theme_app`: idempotent enable/disable of an
      already-installed theme app (GET enabled list, act only on drift, re-GET
      and assert). App id stays a parameter — task 1.3 has not confirmed the NL
      Design System id, so nothing is hardcoded.
- [x] 2.3 Wired into `provision.py all` as step `[11/12]` (before `sync-run`)
      and exposed standalone as `provision.py theme`. Documented in
      `docs/provisioner-commands.md`.
- [x] 2.4 `tests/test_provision.py`: 14 unit tests (drift, no-op on converged,
      absent key, blank values ignored, unknown key rejected, write that does
      not reflect, app enable/disable/idempotent/missing-id, CLI flag mapping).
      Mocked HTTP — no live Nextcloud needed.
- [ ] 2.5 Add the NL Design System theme to `webgui/tenants.py::KNOWN_APPS`
      only if it should be selectable from the create-tenant form later (not
      required for MVP — operator can run the provisioner step directly first).

## 3. Custom-domain TLS rendering

- [x] 3.1 Confirm the consuming contract exists — **done 2026-08-07**:
      `react-base/react-platform/argo/applicationsets/react-tenants.yaml`
      (~lines 114–127) reads `frontend.tls.secretName` (default
      `wildcard-openwoo-tls`) and `frontend.tls.issuer` (`none` ⇒ no
      cert-manager annotation). Live example on Nextcloud-base `main`:
      `nextcloud-platform/values/tenants/tenant-oudeijsselstreek-accept.yaml`.
      No Nextcloud-base or react-base change needed.
- [ ] 3.2 `webgui/tenants.py`: render the `frontend.tls` block when
      `frontend.host` is a custom (non-platform) host. Secret name derived from
      the host the way live tenant files do it (dots → dashes, `-tls` suffix);
      `issuer` defaults per design.md Open Question. Update `render()` /
      `validate()`.
- [ ] 3.3 `tests/test_tenants.py`: cover the host→secret-name derivation, the
      platform-host exclusion (no `tls` block for `*.openwoo.app`), and the
      issuer default.
- [ ] 3.4 Write the operator runbook (`docs/custom-domain-cert.md`): merge
      order (PR merges → namespace exists → cert Secret seeded → Ingress picks
      it up), the `certswap apply k8s` path, the `kubectl create secret tls`
      fallback, and recording the cert expiry + owner (no auto-renewal under
      `issuer: none`).
- [ ] 3.5 Confirm monitoring's `CertificateExpiringSoon` rule fires for a
      hand-seeded Secret with no backing `Certificate` object; if not, note it
      as a follow-up for the monitoring repo (do not fix here).

## 4. One-time secret reveal

- [ ] 4.1 New stdlib module (e.g. `webgui/burnstore.py`): `mint(value, ttl)
      -> token`, `reveal(token) -> value | None` (delete-on-read + expiry).
      Encrypt at rest with a key from the pod's existing secret material —
      never plaintext in the store. TTL and rate limit env-tunable.
- [ ] 4.2 `POST /tenant/<name>/secret-link` — operator-gated
      (`_require_operator`), reads `nextcloud-secrets` for `<name>` via the
      minimal RBAC addition below, mints a token, returns the `/reveal/<token>`
      URL. Does not log the secret value.
- [ ] 4.3 `GET /reveal/<token>` — **no auth gate**, single read, plain HTML
      response (no JS dependency), then delete regardless of outcome.
- [ ] 4.4 RBAC: extend `webgui/deploy/rbac-argo.yaml` (or a new
      `rbac-secrets.yaml`) to `get` (not `list`/`watch`) on `Secret
      nextcloud-secrets` per tenant namespace — smallest addition that makes
      4.2 work.
- [ ] 4.5 `tests/test_webgui.py`: happy path, second-read-404, expiry,
      unauthenticated mint attempt rejected, secret value never appears in
      logs/error messages.
- [ ] 4.6 Feature-flag the mint route; **do not enable for operators** until
      1.1 confirms ESO is live end-to-end (mirrors `tenant-creation-pr-flow`
      task 6.2's gating pattern).

## 5. Docs + changelog

- [ ] 5.1 `webgui/README.md`: document the theme step, the `frontend.tls`
      rendering + cert runbook, and the reveal-link flow (mint vs. reveal, who
      can do what).
- [ ] 5.2 `CHANGELOG.md` updated.

## 6. Dry-run + done criteria

- [ ] 6.1 Theme step: run against a throwaway tenant, confirm idempotent
      re-run is a no-op.
- [ ] 6.2 TLS rendering: create a throwaway custom-domain tenant, confirm the
      PR carries a `frontend.tls` block identical in shape to the live examples
      and that Nextcloud-base CI accepts it.
- [ ] 6.3 Reveal flow: mint a link for a throwaway tenant's secret, confirm
      one read works, a second read 404s, and an expired token 404s.
- [ ] 6.4 Done: a PO can receive a working custom-domain, themed Nextcloud
      and its initial password via a link — with zero devops-person
      involvement after the PR is merged.
