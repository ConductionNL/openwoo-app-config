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

`provision-job.yaml` / `provision-cronjob.yaml` are **Helm templates** that drop
straight into `nextcloud-platform`'s `tenant-resources/templates/` — they follow
that chart's conventions (`.Values.tenant.name/namespace`,
`tenant-resources.labels`, hardened securityContext, Argo hooks). They read the
tenant admin from `nextcloud-secrets` (`nextcloud-username` / `nextcloud-password`)
and target the in-cluster `nextcloud` service.

1. Build + push the image (config baked in, per tag): `make image push`
   (`IMAGE`/`TAG`/`CONTAINER` overridable).
2. Drop `provision-job.yaml` into `tenant-resources/templates/` (Argo PostSync
   hook → runs after Nextcloud is up). Optionally `provision-cronjob.yaml` for
   drift reconciliation.
3. Add the `woo` values block (below) to the tenant values.

Both run `provision.py all --skip-credentials`. Because every step is
**idempotent** (GET-check, skip when converged) they are safe per sync and on a
schedule — converged runs are near no-ops.

## Values

```yaml
woo:
  enabled: true                # render the WOO provisioning Job for this tenant
  provisionerImage: conduction2022/openwoo-provisioner:<tag>
  base: http://nextcloud       # in-cluster service (or the ingress https URL)
  openCatalogiBase: true       # false -> adds --skip-oc-settings --skip-catalog
  reconcile: false             # true -> also render the reconcile CronJob
  reconcileSchedule: "*/30 * * * *"
```

## Notes

- Per-tenant **source connection** (URL / API-Interface-ID / API key) is NOT set
  here — an operator sets it via the CLI/GUI (`provision.py credentials …`).
- On a config *content* change, ship a new image tag; the changed slugs trigger
  an import. `--force-import` exists for the rare forced re-upload.
