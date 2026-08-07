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
- [x] 1.3 Confirm what "the NL Design System theme" is — **done 2026-08-07: it
      is the react frontend's theme classname, not a Nextcloud app.**
      `frontend.branding.themeClassname` → `GATSBY_NL_DESIGN_THEME_CLASSNAME`
      (`react-tenants.yaml:111,175`), baseline `conduction-theme`. No Nextcloud
      app id to confirm; the app-enable path was a false lead.

## 2. Branding at tenant creation

**Premise correction (2026-08-07).** The proposal said branding is one string
(`frontend.branding.organisationName`) and that theming needed a new provisioner
step. Both were wrong. `frontend.branding` already carries `organisationName`,
`themeClassname`, `jumbotronImageUrl` and `faviconUrl`; the `react-tenants`
ApplicationSet turns each into a `GATSBY_` env var; 24 of 78 tenant files
already use `themeClassname` (`tenant-tubbergen-accept.yaml:22-25`). A
Nextcloud-back-office theming step was built and reverted — it addressed a
different surface that nobody asked for.

The real gap was narrow: `webgui/tenants.py::render()` emitted only
`organisationName`, so a tenant created from the form landed on the
`conduction-theme` baseline and someone had to hand-edit the tenant file
afterwards — the manual devops step this change exists to remove.

- [x] 2.1 `webgui/tenants.py`: `from_org()` accepts theme/jumbotron/favicon and
      `render()` emits them under `frontend.branding`, in the shape the
      ApplicationSet reads. Blank values are omitted, never emitted empty.
- [x] 2.2 Blank theme stays blank on purpose: the ApplicationSet falls back to
      `conduction-theme`, which ships with the bundled themes and renders.
      Deriving `<org>-theme` is the 2026-06-30 bug where onboarded tenants
      rendered with no theme at all. Recorded in `render()`'s docstring.
- [x] 2.3 `webgui/templates/tenant.html` + `server.py::tenant_create`: three
      optional fields under the advanced section, with the "leave blank unless
      the organisation ships its own theme" hint.
- [x] 2.4 `tests/test_tenants.py` + `tests/test_webgui.py`: rendering, blank
      omission, extras without `organisationName`, pass-through in `from_org`,
      and the fields reaching the proposed PR content.
- [ ] 2.5 (follow-up, not in this change) Existing tenants are unaffected: the
      appset ignore-diffs the branding env (`^(GATSBY_|NL_DESIGN_)`), so a value
      added to a live tenant file does not reach a running frontend. If existing
      tenants need their branding brought under git, that is its own change with
      its own rollout — creation is the only moment this path covers.

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

- [ ] 6.1 Branding: create a throwaway tenant with a `themeClassname` from the
      form, confirm the PR carries the branding block and that the frontend
      comes up on that theme (not on `conduction-theme`).
- [ ] 6.2 TLS rendering: create a throwaway custom-domain tenant, confirm the
      PR carries a `frontend.tls` block identical in shape to the live examples
      and that Nextcloud-base CI accepts it.
- [ ] 6.3 Reveal flow: mint a link for a throwaway tenant's secret, confirm
      one read works, a second read 404s, and an expired token 404s.
- [ ] 6.4 Done: a PO can receive a working custom-domain, themed Nextcloud
      and its initial password via a link — with zero devops-person
      involvement after the PR is merged.
