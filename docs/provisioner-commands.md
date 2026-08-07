---
last_reviewed: 2026-08-07
owner: info@conduction.nl
---

# Provisioner command reference

A real tenant bring-up is more than the import. `provision.py` performs
the post-install steps the config owns, over the API, each asserting it
took effect ("test what you ship"). Every step is one subcommand with its
own unit tests. The logic lives in the `provisionlib` package
(`constants` / `helpers` / `client` / `steps` / `cli`); `provision.py` is
a thin entrypoint, and callers can also `import provisionlib as
provision` to reuse the steps as a library.

| Subcommand | Does | Asserts |
|------------|------|---------|
| `settings` | PUT organisation + multitenancy settings | GET reflects the sent fields |
| `import` | upload the config | response reports "Import successful" |
| `authorization` | (repair) set/flip schema authorization flags (`inheritFromPublic`) on a tenant | the flag reflects on each schema |
| `oc-settings` | couple OpenCatalogi object types to their register + schema | GET reflects (slugs resolved to tenant ids) |
| `verify-import` | compare config slugs to the tenant | every register/schema/source/sync present |
| `sync-check` | inspect tenant synchronizations | every target schema resolved (no dangling `reg/<slug>`) |
| `credentials` | set each source's `headers.API-KEY` | GET reflects the key |
| `sync-run` | POST run/`--test` per synchronization | no error (real run fetches live data) |
| `jobs` | resolve each job's `synchronizationId` (sync slug → tenant numeric id) | the job reflects the numeric id |
| `objects` | create one object in a register/schema | response carries an id/uuid |
| `catalog` | point the OpenCatalogi catalog at the WOO register + all its schemas | registers/schemas reflect (slugs resolved to tenant ids) |
| `delete-menu` | delete the OpenCatalogi default `User Menu` object (not part of the WOO config) | GET no longer lists it (idempotent — skips when absent) |
| `theme` | converge Nextcloud theming (name, slogan, colour, urls) and optionally enable a theme app | GET reflects each written key; the app appears in the enabled list |
| `all` | run the bring-up in order, gating each step | settings → verify-import → credentials → sync-check → theme → (`--run-syncs`) |

`verify-import` and `sync-check` exist because the import API returns
HTTP 200 even when it silently drops rows: on a tenant that already holds
data the bulk row count can't see the gap, but a slug-level diff can.
(They caught exactly this on canary — see
[notes/PROVISIONING-TEST-PLAN.md](notes/PROVISIONING-TEST-PLAN.md).)

Connection flags are shared: `--base`, `--user`, and `--password` /
`--password-env` (the env form keeps the secret out of argv). Steps that
read the config also take `--config`.

## Theming

`theme` converges the Nextcloud `theming` app's settings the same way the
other steps converge OpenRegister config: read what is there, write only
what differs, read back and assert. Flags map one-to-one onto Nextcloud
theming keys:

| Flag | Theming key |
|---|---|
| `--theme-name` | `name` |
| `--theme-slogan` | `slogan` |
| `--theme-color` | `color` |
| `--theme-url` | `url` |
| `--theme-imprint-url` | `imprintUrl` |
| `--theme-privacy-url` | `privacyUrl` |
| `--theme-app` | (not a key — a Nextcloud app id to enable) |

A flag you leave out is left untouched on the tenant, and a blank value
is ignored rather than written: a half-filled form must not silently wipe
a tenant's branding. Clearing a value is therefore a deliberate act in
the Nextcloud admin UI, not a side effect of re-running the provisioner.

Theming lives in the database (`oc_appconfig`), not in `config.php`, so it
survives the pod restart that Nextcloud-base's
[CONFIG-CHANGES.md](https://github.com/ConductionNL/Nextcloud-base/blob/main/docs/CONFIG-CHANGES.md)
warns about for system config. These are the same settings
`occ theming:config <key> <value>` writes, reached over the OCS app-config
API so no `kubectl exec` is needed.

**Logo, background and favicon are not covered.** Those are file uploads
to the theming app's session- and CSRF-protected ajax route, not
app-config values, so they cannot be set over the basic-auth API every
other step uses. Upload them once in the tenant's admin UI, or track a
follow-up if it needs automating.

The same values are exposed as a **Huisstijl** fieldset on the webgui's
"Omgeving inrichten" form, which builds exactly these flags — so the
browser route and the CLI route converge a tenant identically.

`--theme-app` enables (or, with `--disable-theme-app`, disables) one
already-installed Nextcloud app — e.g. an NL Design System theme. It does
**not** install an app from the app store; an unknown app id surfaces as
an OCS error.
