# Running the provisioner in-cluster (GitOps / Argo)

This is the **target track** wired into the deploy pipeline. `Nextcloud-base`
(the Argo ApplicationSet) brings up a tenant; then the provisioner converges its
WOO configuration. Fully GitOps: everything is declarative in git, Argo renders
and reconciles it. **No custom image, no registry, no build step, no token.**

## How (GitOps — recommended)

`provision.py` + the tagged `woo.configuration.json` are ~300 KB together — well
under the 1 MiB ConfigMap limit. So they ship as a **ConfigMap**, mounted into a
**stock public `python:3-slim`** image that runs them:

- `provision-configmap.yaml` — Helm template, renders the ConfigMap from the two
  files vendored into the chart (`files/openwoo/provision.py`,
  `files/openwoo/woo.configuration.json`) at the pinned `openwoo-app-config` tag.
- `provision-job.yaml` — Argo **PostSync** Job: mounts the ConfigMap, runs
  `python3 /provisioner/provision.py all --skip-credentials`.
- `provision-cronjob.yaml` — optional drift reconcile (`.Values.woo.reconcile`).

All three are Helm templates that drop into `nextcloud-platform`'s
`tenant-resources/templates/` and follow that chart's conventions
(`.Values.tenant.*`, `tenant-resources.labels`, hardened securityContext, Argo
hooks). Admin creds come from `nextcloud-secrets`
(`nextcloud-username` / `nextcloud-password`); `--base` is the in-cluster
`nextcloud` service.

### Vendoring the two files

The platform pins `openwoo-app-config` at a tag (as it already does for the
config). Place the pinned files at `files/openwoo/provision.py` and
`files/openwoo/woo.configuration.json` in the chart (e.g. an Argo multi-source
Application, a git submodule, or a sync step). The ConfigMap is then rendered
from them — a config change is a new tag → new ConfigMap → Job re-runs.

## Values

```yaml
woo:
  enabled: true                 # render the WOO provisioning resources
  pythonImage: python:3.12-slim # stock runtime (override for a mirror)
  base: http://nextcloud        # in-cluster service (or the ingress https URL)
  openCatalogiBase: true        # false -> adds --skip-oc-settings --skip-catalog
  forceImport: false            # true -> --force-import (re-upload on content change)
  reconcile: false              # true -> also render the reconcile CronJob
  reconcileSchedule: "*/30 * * * *"
```

## Notes

- Per-tenant **source connection** (URL / API-Interface-ID / API key) is NOT set
  here — an operator sets it via the CLI/GUI (`provision.py credentials …`).
- Idempotent: every step GET-checks and skips when converged, so the Job/CronJob
  are safe per sync and on a schedule.
- `Dockerfile` + `make image/push` remain as an **optional fallback** (e.g. if
  the config ever outgrows the 1 MiB ConfigMap limit, or an air-gapped cluster
  can't pull `python:3-slim`). The GitOps ConfigMap path above is the default.
