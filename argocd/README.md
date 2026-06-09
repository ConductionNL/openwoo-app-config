# Argo app config

`applicationset.yaml` is the **example** that makes Argo deploy this repo's WOO
provisioning per tenant. It belongs in your **GitOps repo**
(`nextcloud-platform/argo/applicationsets/`), not here — `openwoo-app-config` is
the *source* (config + `provision.py` + the root `kustomization.yaml`); the
ApplicationSet is the *deployment* of that source.

## What it does

Each generated Application renders this repo's root `kustomization.yaml` at a
**pinned tag** and applies it to the tenant namespace (`nc-<tenant>`):

- a `ConfigMap` (`provision.py` + the tagged `woo.configuration.json`, via
  `configMapGenerator` — no custom image), and
- an Argo **PostSync** `Job` that mounts it and runs
  `provision.py all --skip-credentials` (idempotent; re-converges each sync).

The resources carry no namespace, so they land in the Application's
`destination.namespace`. Admin creds come from the tenant's `nextcloud-secrets`;
`--base` is the in-cluster `nextcloud` service.

## To wire it up

1. Pin `targetRevision` to a released tag of this repo.
2. Replace the `list` generator with the tenant generator the platform already
   uses (so it tracks the same tenants).
3. The per-tenant **source connection** (source URL / API-Interface-ID / API key)
   is set out-of-band by an operator (CLI/GUI), never by this ApplicationSet.
4. Tenant without the OpenCatalogi base? Add `--skip-oc-settings --skip-catalog`
   to the Job args (a kustomize patch, or a small overlay).
