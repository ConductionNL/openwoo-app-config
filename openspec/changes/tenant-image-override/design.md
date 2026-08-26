## Context

`webgui/tenants.py` is deliberately pure: stdlib `re` only, no YAML dependency,
no I/O. Every function takes a dict and returns strings or lists of error
strings. That is why the 311-test suite runs in ~1.5s, and it is the property
this change must not spend.

The guard needs the opposite: it has to read `values/common.yaml` from git, read
an Argo Application, and query commit history. So the split is not incidental —
it is the whole design.

`_tenant_write()` in `webgui/server.py` is the single write path for create and
update alike. It already sequences: `validate_org` → `from_org` → `validate` →
`_declaration` → PR. The guard slots in after `validate` and before the PR, which
is the only place where both "the proposal is well-formed" and "we may still talk
to git/Argo" hold.

## Goals / Non-Goals

**Goals**
- Pin a non-default Nextcloud image from the portal.
- Refuse a downgrade before a PR exists, naming both versions.
- Surface a re-add of a previously removed tenant, including its old `dbType`.
- Restore editability for tenants that carry an `image:` block.
- No new cluster privileges. No new third-party dependencies.

**Non-Goals**
- PVC-installed-version comparison (needs `exec`; see proposal Non-Goals).
- GHCR tag existence. `digest:` support.

## Decisions

### Pure comparison, injected readers

`tenants.py` gets two pure functions and nothing else new:

- `image_version(tag)` → the version part of `32.0.6-fpm-soap` as a comparable
  tuple, or `None` when the tag carries no version.
- `compare_versions(a, b)` → `-1 | 0 | 1`.

The version must be parsed, not string-compared. Lexically
`"32.0.6-fpm-soap"` is **greater** than `"32.0.13-fpm"`, because `'6' > '1'`. A
string compare therefore concludes 32.0.6 is *newer* than 32.0.13 and lets the
downgrade through — it says yes to exactly the case it exists to catch. That
single trap is why this gets its own function and its own tests.

The reads live in `server.py` and are passed in. A guard function receives the
already-fetched current tag rather than fetching it, so its logic is testable
without mocking urllib.

### Rejecting floating tags

`_TAG_RE` already rejects a full reference with `/` or `:`. It accepts
`fpm-soap`, because as a *tag* that is well-formed. The new rule is semantic:
a Nextcloud image tag must contain a version, because with
`pullPolicy: IfNotPresent` a floating tag makes the running version depend on
when a node last pulled. Demonstrated 2026-08-19: `fpm-soap` moved from
`sha256:31123c8c` to `sha256:80310a36` with no change in git.

So `image_version()` returning `None` is itself the error for the Nextcloud
image. The frontend tag keeps its current, laxer rule — its ApplicationSet has
different semantics and existing tenants rely on it.

### Layer 2 is a cross-check, not the truth

Git is authoritative for what *should* run; Argo reports what it *sees*. When
they disagree, git wins for the block decision and the disagreement becomes a
warning. Reversing that would let a drifted cluster silently veto a correct
change.

`status.summary.images` lists every image in the Application, frontend included.
The Nextcloud one is selected by repository, not by position.

### Layer 3 warns, never blocks

The portal cannot see namespaces and should not be given permission to. So it
cannot know whether a volume survived. Blocking on a maybe would make re-adding
any removed tenant impossible; staying silent is what let PR #100 through. A
warning that names the removed file's `dbType` and `image` is the honest middle,
and it is the same information a reviewer would have had to dig out of git
history by hand.

### Failure of a read is not a pass

If git or Argo cannot be reached, the guard cannot conclude "no downgrade". It
must say so and refuse, the way `_tenant_write()` already refuses when
`_declaration()` raises: *"Cannot tell create from update: refuse rather than
guess."* Same reasoning, same behaviour — a guard that fails open is not a guard.

## Risks

- **False block on a suffix-only change.** `32.0.6-fpm` → `32.0.6-fpm-soap` is
  the same version with a different build. `compare_versions` returns 0 there, so
  it is allowed; the tests pin that case explicitly.
- **Layer 2 latency.** One extra Argo read per submit, on a form a human just
  filled in. Acceptable; `argolib` already backs the landing page.
- **The rule still lives in two places** — this guard and Nextcloud-base's prose.
  The prose stays the reference; this change cites it rather than restating it.
