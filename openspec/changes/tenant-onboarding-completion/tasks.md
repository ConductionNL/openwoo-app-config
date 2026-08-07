## 1. Prerequisites — confirm before building

- [x] 1.1 ESO is **not** a dependency — **premise corrected 2026-08-07.**
      Nextcloud-base `docs/SECRETS.md`: *every* tenant ends up with a Secret
      `nextcloud-secrets` in its namespace; only the mechanism differs
      (`create-tenant-secret.sh` for existing tenants, ESO for managed ones).
      Both produce the same keys, so the reveal flow reads the same thing
      either way. The real precondition is per tenant — does that Secret exist
      — which the mint route now checks before minting.
      Two corrections that came out of the same page: the namespace is the
      **bare tenant name**, not `nc-<tenant>` (that is the Argo application),
      and the admin key is **`nextcloud-password`**, not `admin-password`.
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
- [x] 3.2 `webgui/tenants.py`: `is_custom_frontend_host()` +
      `tls_secret_name()`; `render()` emits the `frontend.tls` block only for a
      host outside `openwoo.app`, and `validate()` rejects an unknown issuer (a
      typo would otherwise become a cert-manager annotation nobody resolves).
      Secret name derived from the host exactly as the fleet does it.
- [x] 3.3 `tests/test_tenants.py` + `tests/test_webgui.py`: derivation
      (including case, trailing dot, and a lookalike domain like
      `evilopenwoo.app`), the platform-host exclusion, both issuer values, the
      default, validation, and the block reaching the proposed PR.
- [x] 3.4 `docs/custom-domain-cert.md`, linked from `docs/index.md`: why the
      certificate is not in git, what the form writes, merge order, the
      `certswap` path with the `kubectl create secret tls` fallback, verifying
      with `openssl s_client`, recording expiry + owner, and troubleshooting
      (including the "Let's Encrypt overwrote a paid cert" case).
- [x] 3.5 Confirmed — **the alert does NOT cover a hand-seeded Secret.**
      `CertificateExpiringSoon` fires on
      `certmanager_certificate_expiration_timestamp_seconds`, which cert-manager
      only produces for a `Certificate` object; `issuer: none` creates none.
      Recorded as a gap in the runbook and left as a follow-up for the
      monitoring repo, not fixed here. A fix would need a probe reading the
      Secret or the live endpoint.

**Issuer default decided (2026-08-07): `none`.** The failure modes are not
symmetric. A missing certificate is loud — the browser complains and it gets
fixed. A wrongly-issued one is quiet: Let's Encrypt overwrites a paid
certificate and nobody notices until the customer does. That is precisely the
bug Nextcloud-base's `fix/klantcertificaten-issuer-none` branch exists to
repair. Both values are live in the fleet, so the form offers the choice;
only the default is opinionated.

## 4. One-time secret reveal

- [x] 4.1 `webgui/burnstore.py` (stdlib): `mint(tenant, requested_by, ttl)
      -> token`, `claim(token) -> entry | None` (burn-on-read + expiry), and
      `read_admin_password(tenant)`. TTL, ticket cap and token size env-tunable.
      **Deviation from design.md, deliberate:** the store holds **no secret
      material** rather than an encrypted copy. The stdlib has no authenticated
      cipher and hand-rolling one is worse than the problem; "never stored" also
      beats "encrypted with a key in the same pod". Only `sha256(token)` is
      persisted, so the stored form cannot be replayed as a link. Storage is one
      ConfigMap in the portal's own namespace, so a pod restart does not turn a
      valid link into "already used".
- [x] 4.2 `POST /tenant/<name>/secret-link` — operator-gated, validates the
      tenant name, fails fast when there is no readable password (the operator
      finds out, not the product owner), mints, returns the URL. The value is
      not in the response and not in any log line.
- [x] 4.3 `GET /reveal/<token>` — no auth gate, burns the ticket **before**
      fetching, plain JS-free page (`templates/reveal.html`, `noindex`,
      `no-referrer`). Expired and already-used are the same 404 so a probe
      learns nothing.
- [x] 4.4 RBAC: new `webgui/deploy/rbac-secrets.yaml`, wired into the
      kustomization. ClusterRole `get` on Secrets restricted by
      `resourceNames: [nextcloud-secrets]`, no `list`/`watch`; plus a
      *namespaced* Role for the ticket ConfigMap. Documented honest limit: the
      Secret also holds S3/DB/Redis creds and RBAC cannot scope per key, so
      `read_admin_password()` is the boundary.
- [x] 4.5 `tests/test_burnstore.py` (15) + `tests/test_webgui.py` (9):
      mint/claim, second-read 404, expiry, sweep, store-full, token uniqueness,
      no secret material in the stored ticket, the right namespace and key,
      unauthenticated mint rejected while reveal passes the gate, flag off by
      default, and an assertion that the password never reaches the logs.
- [x] 4.6 `REVEAL_ENABLED` defaults to **false**; both routes 404 until a
      deployment turns it on deliberately.

## 5. Docs + changelog

- [x] 5.1 `docs/secret-reveal.md` (+ linked from `docs/index.md`): the reveal
      flow, why the reveal route is unauthenticated, what it reads, the RBAC
      limit, and the env knobs. Written in `docs/` rather than a new
      `webgui/README.md` so the handbook/MCP indexes it like every other page.
      The TLS runbook follows with section 3.
- [ ] 5.2 `CHANGELOG.md` updated.

## 6. Dry-run + done criteria

- [ ] 6.1 Branding: create a throwaway tenant with a `themeClassname` from the
      form, confirm the PR carries the branding block and that the frontend
      comes up on that theme (not on `conduction-theme`).
- [ ] 6.2 TLS rendering: create a throwaway custom-domain tenant, confirm the
      PR carries a `frontend.tls` block identical in shape to the live examples
      and that Nextcloud-base CI accepts it.
- [ ] 6.3 Reveal flow: with `REVEAL_ENABLED=true` on a deployment, mint a link
      for a throwaway tenant, confirm one read works, a second read 404s, and an
      expired token 404s. Also confirm the RBAC is sufficient (the pod can `get`
      `nextcloud-secrets` in a tenant namespace) and no wider (it cannot `list`
      Secrets).
- [ ] 6.4 Done: a PO can receive a working custom-domain, themed Nextcloud
      and its initial password via a link — with zero devops-person
      involvement after the PR is merged.
