## Why

A tenant that needs a different Nextcloud build cannot get one through
`platform.commonground.nu`. The portal renders a fixed set of keys
(`RENDERED_TOP_KEYS = {"tenant"}`, `webgui/tenants.py`) and a **top-level**
`image:` block is not among them. It can pin the *frontend* image
(`tenant.frontend.registry/repository/tag`); it cannot pin the Nextcloud image.

Two consequences, both live today:

- Such a tenant is hand-written as a PR on Nextcloud-base, outside every check
  this portal performs.
- `unknown_keys()` reports `image` as unmodelled, so `_declaration()` marks the
  tenant **read-only**. Once a tenant has an image override, the portal can no
  longer manage it at all — the operator loses the create *and* the edit path.

The reason it was left out was never effort. It is that the rule which makes an
image override safe is the one thing a form cannot express by itself:
Nextcloud-base `docs/ADDING-TENANT.md` — *never point an existing tenant at a
lower version*. `/var/www/html` is a PVC, the upstream entrypoint exits 1 on an
older image ("downgrading is not supported"), and with `selfHeal: true` Argo
retries forever. Recovery is reverting the tenant file, not `kubectl`.

That rule was nearly broken twice in one month:

- **2026-08-25** (Nextcloud-base CHANGELOG): asked whether tenants support a
  different image, the portal answered with a reconstruction that presented a
  32.0.13 → 32.0.6 downgrade as routine, and cited `beek` as precedent — a
  legacy standalone Application on chart 6.4.1, not a tenant of the
  `nextcloud-tenants` ApplicationSet.
- **2026-08-26**, Nextcloud-base PR #100 `add tenant: harderwijk-prod`: a re-add
  of a tenant removed one day earlier, pinned to `32.0.6-fpm-soap` while the
  previous version of that same file carried no override and therefore ran
  32.0.13. It landed safely because the namespace turned out to be cleaned up —
  not because anything stopped it.

So the change is not "add three input fields". It is: offer the field **and**
build the guard the manual route does not have. Adding the fields alone would put
the dangerous choice one dropdown away for every operator, which is precisely the
argument for keeping it out.

## What Changes

- **`webgui/tenants.py`** — `RENDERED_TOP_KEYS` gains `image`, so a tenant with
  an override becomes editable instead of read-only. `render()` emits a top-level
  `image:` block when supplied, mirroring the existing frontend image-pin branch
  (three separate fields, `_q()` quoting). `from_declaration()` reads it back.
  `validate()` enforces the tag shape and **requires a version number**, so
  floating tags (`fpm-soap`, `latest`) are rejected. New pure helpers
  `image_version()` and `compare_versions()` — no I/O, so the comparison logic is
  unit-testable on its own.
- **`webgui/argolib.py`** — `_summary()` also returns `images` from
  `status.summary.images`. The full Application is already fetched in `_get()`,
  so this costs no extra call and **no new RBAC**: the portal stays read-only on
  `argoproj.io` Applications in `argocd`.
- **`webgui/gitlib.py`** — new `file_history(path, limit)` over
  `GET /repos/{repo}/commits?path=…`, to detect that a tenant file existed before
  and was removed. `get_file(path, ref)` already covers reading `common.yaml`.
- **`webgui/server.py`** — the guard runs in `_tenant_write()`, the single write
  path for both create and update, after `tenants.validate()` and before the PR
  is opened. Blocks return `{"errors": [...]}, 400` as today; a new `warnings`
  field carries what must be seen but must not block.
- **Templates** — three fields (registry / repository / tag) behind a collapsed
  "Afwijkende Nextcloud-image" section in `tenant.html` and `edit.html`, with the
  reason and the three rules beside them. Deliberately not prominent: this is an
  exception, not a peer option. `warnings` rendered.
- **No `digest:` field, ever** — chart 8.9.0 does not render it, so the podspec
  would carry the tag only while git claimed a digest. Not offering it is the fix.
- **No GHCR existence check** — that would make form validation depend on an
  external service. A non-existent tag surfaces as `ImagePullBackOff` on first
  sync: visible, not silent.

### The guard, three layers

| Layer | Source | Outcome |
|---|---|---|
| 1. git | effective current tag: `image.tag` from the tenant file, else `image.tag` from `values/common.yaml` | **block** a lower version |
| 2. argo | `status.summary.images` of `nc-<tenant>` | **block** a lower version; disagreement with layer 1 is a warning |
| 3. history | did `tenant-<name>.yaml` exist before and get removed? | **warn**: the namespace may still hold a PVC |

Layer 3 is the harderwijk case, and it is exactly where layers 1 and 2 know
nothing: the file is gone and the Argo Application has been pruned. Both
ApplicationSets set `preserveResourcesOnDeletion: true`, so the volume can still
be there. Detecting it through namespaces would need new cluster rights;
`argolib` is deliberately least-privilege. Through git history it needs none.

Layer 3 warns rather than blocks — whether the volume is still there is
genuinely unknown to the portal. The warning names the previous `dbType` and
`image` from the removed file, so a database engine switch becomes visible. That
is the part of PR #100 nobody caught: the file it replaced was `postgres`.

## Capabilities

### New Capabilities

- `tenant-image-override`: an operator pins a non-default Nextcloud image from
  the portal, with the version rules enforced instead of documented.
- `image-downgrade-guard`: a proposed image that is older than what the tenant
  runs is refused before a PR exists, with the compared versions named.
- `tenant-readd-warning`: re-creating a previously removed tenant surfaces the
  removed file's `dbType` and `image`, and the possibility of a retained volume.

### Modified Capabilities

- `tenant-create-via-form` / tenant edit: a tenant carrying an `image:` block is
  no longer forced read-only by `unknown_keys()`.

## Non-Goals

- **Comparing against the version installed in the PVC.** The guard compares
  against the *podspec* image (git and Argo). Those normally agree; after a
  failed upgrade they do not. Closing that gap needs `exec` in the tenant
  namespace — far wider rights than `argolib` holds. Layer 3's warning covers the
  practical case.
- **Verifying a tag exists in GHCR** (see above).
- **Offering `digest:`** (see above).
