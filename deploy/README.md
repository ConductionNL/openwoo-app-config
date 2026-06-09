# Running the provisioner in-cluster (Argo)

This is the **target track** wired into the deploy pipeline. `Nextcloud-base`
(the Argo ApplicationSet) brings up a tenant; then the provisioner converges its
WOO configuration. No vendor CI, no external trigger — it runs in-cluster, right
after the deploy, as a Kubernetes Job that Argo manages per tenant.

## The split

| Concern | Owner |
|---------|-------|
| **Base config** — settings, oc-settings, import, verify-import, catalog, sync-check, jobs | **Argo** — declarative, the Job here (`provision.py all --skip-credentials`) |
| **Per-tenant source connection** — source URL, API-Interface-ID, API key | **Operator** — out-of-band via the CLI/GUI (`provision.py credentials …`), never in Argo |

The base config is the same on every tenant (it's the tagged
`config/woo.configuration.json`); the source connection differs per client, so it
is supplied by an operator, not the reconciler.

## How it runs

1. Build + push the image (config baked in, per tag): `make image push`
   (`IMAGE`/`TAG`/`CONTAINER` overridable).
2. Add the per-tenant Job to the ApplicationSet (`provision-job.yaml`) — an Argo
   **PostSync hook** so it runs after the Nextcloud app + OpenCatalogi base are
   healthy. Admin creds come from the tenant's existing admin secret (envFrom
   `secretKeyRef`); `--base` is the in-cluster service (or the ingress URL).
3. Optionally add `provision-cronjob.yaml` for continuous drift reconciliation.

Both run `provision.py all --skip-credentials`. Because every step is
**idempotent** (GET-check, skip when converged) the Job/CronJob is safe to run on
every sync and on a schedule — converged runs are near no-ops.

## Notes

- `--skip-oc-settings --skip-catalog` if a tenant has no OpenCatalogi base.
- If the config *content* changes, the import is still skipped on slug-presence;
  bump behaviour with `--force-import` (rarely needed in the Job — a new image
  tag carries the new config and the changed slugs trigger an import anyway).
- The two manifests are **examples**: fill the `<PLACEHOLDERS>` and adapt to the
  ApplicationSet's templating (namespace, secret names, image tag).
