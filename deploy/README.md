# In-cluster provisioning manifests (GitOps / Argo)

The **target track** wired into the deploy pipeline. `Nextcloud-base` (Argo)
brings up a tenant; then this converges its WOO config. Fully GitOps: everything
is declarative in git and Argo renders + reconciles it — **no custom image, no
registry, no build step, no token.**

## Shape

This repo is a **kustomize app** (root `../kustomization.yaml`):

- `configMapGenerator` turns `scripts/provision.py` + `config/woo.configuration.json`
  into a ConfigMap (~300 KB, under the 1 MiB limit; read from the repo at the
  pinned revision — no vendoring).
- `provision-job.yaml` — an Argo **PostSync** Job that mounts that ConfigMap into
  a stock `python:3-slim` and runs `provision.py all --skip-credentials`.
- `provision-cronjob.yaml` — optional drift reconcile (uncomment in the root
  kustomization to enable).

The resources carry **no namespace**, so an Argo Application drops them into its
`destination.namespace` (the tenant's). Admin creds come from the tenant's
`nextcloud-secrets` (`nextcloud-username` / `nextcloud-password`); `--base` is the
in-cluster `nextcloud` service. See `../argocd/` for the ApplicationSet that
deploys this per tenant.

## Verify locally

```bash
kubectl kustomize .        # from the repo root — renders ConfigMap + Job
```

## Notes

- Per-tenant **source connection** (URL / API-Interface-ID / API key) is NOT set
  here — an operator sets it via the CLI/GUI (`provision.py credentials …`).
- Idempotent: every step GET-checks and skips when converged, so the Job/CronJob
  are safe per sync and on a schedule. A config change is a new tag → new
  ConfigMap hash → the Job re-runs.
- Tenant without the OpenCatalogi base: add `--skip-oc-settings --skip-catalog`
  to the Job args.
- `../Dockerfile` + `make image/push` remain an **optional fallback** (config >
  1 MiB ConfigMap limit, or air-gapped clusters that can't pull `python:3-slim`).
