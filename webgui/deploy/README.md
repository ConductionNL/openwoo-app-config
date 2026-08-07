# Deploy (Phase 3): the provisioning control-plane on Kubernetes

Deploys the OpenWoo provisioning web GUI behind oauth2-proxy → Keycloak, at
`https://platform.commonground.nu`.

```
Ingress (nginx, TLS via cert-manager)
   └─▶ Service :80 ──▶ pod :4180  oauth2-proxy  ──auth──▶ Keycloak ──▶ Google
                                       │ (X-Forwarded-Email)
                                       ▼
                                  app 127.0.0.1:8081  (gunicorn, Flask)
```

One pod, two containers. The app binds **localhost only**; oauth2-proxy is the
sole network listener. A NetworkPolicy additionally allows pod ingress **only**
from the `ingress-nginx` namespace on `:4180`. Together that enforces the trust
anchor for `X-Forwarded-Email` (the Phase-2 review follow-up, now code not prose).

## Manifests

| File | What |
|---|---|
| `namespace.yaml` | `openwoo-platform` namespace |
| `serviceaccount.yaml` | SA with token automount off |
| `deployment.yaml` | app (gunicorn, localhost) + oauth2-proxy sidecar; hardened securityContext |
| `service.yaml` | ClusterIP `:80 → :4180` |
| `ingress.yaml` | nginx + letsencrypt-prod TLS; buffering off for streaming |
| `networkpolicy.yaml` | ingress only from ingress-nginx |
| `networkpolicy-egress.yaml` | egress: DNS, extern 443-only, kube-API, in-cluster HTTP — apart object, onafhankelijk terugdraaibaar; risico-analyse + testchecklist in de file-kop |
| `oauth2-proxy.cfg` | proxy config (Keycloak OIDC; → ConfigMap via kustomize) |
| `secret.example.yaml` | **template** — real Secrets created out-of-band (oauth, git, assistant) |
| `argocd-application.example.yaml` | example Argo App (lives in the GitOps repo) |

## Prerequisites

1. **Keycloak client + Google IdP** in realm `commonground` (KeyCloak repo,
   `realm-commonground.yaml`): client `openwoo-provisioner`, redirect
   `https://platform.commonground.nu/oauth2/callback`. See `../auth/README.md`.
2. **DNS** `platform.commonground.nu` → the ingress LB.
3. **Image** built and pushed (see below).
4. **Secret** `openwoo-provisioner-oauth` created out-of-band:
   ```bash
   kubectl create secret generic openwoo-provisioner-oauth -n openwoo-platform \
     --from-literal=client-secret='<keycloak client secret>' \
     --from-literal=cookie-secret="$(openssl rand -base64 32)"
   ```
5. **Secret** `openwoo-assistant` (platform-assistent; optioneel — zonder dit
   secret geeft `/assistant` een 503 en draait de rest gewoon). Zie
   `secret.example.yaml` voor keys en aanmaak-commando. Testfase: persoonlijke
   sub-token toegestaan (vastgelegde afwijking); definitief: org-workspace-key
   via ESO (besluit 1.1 van de change).

## Build & push the image

The image lives on **`ghcr.io/conductionnl/openwoo-provisioner`**. Docker Hub is
the old home: the Docker Pro PAT expired 2026-08-03 and is not being renewed, so
the fleet pulls Docker Hub anonymously under the 100-pulls/6h limit again
(`cluster-config/docs/mirror.md`). That document also sets the convention —
Conduction's own images are published to `ghcr.io/conductionnl` from their own
pipeline, deliberately *not* mirrored, so there is one source of truth per tag.

**Merging is deploying.** `.github/workflows/image.yml` runs on every push to
`main`: it builds `sha-<short>`, verifies the tag is really pullable, writes
that tag into `kustomization.yaml` and commits it back. Argo takes it from
there. Nothing to tag, nothing to bump, no second PR.

The gate is the pull request. Review happens there, `.github/workflows/ci.yml`
runs the same hooks you run locally, and once it is merged the rollout is
bookkeeping — which is a machine's job. An earlier version of this made a human
tag a release and open a follow-up PR for the bump; that added no safety and one
ordering mistake to make (bump merged before the image existed → Argo pointed at
a tag it could not pull). Build and bump now happen in one job, in that order.

**Release tags are markers, not deploys.** Pushing `v0.7.0` (or dispatching the
workflow with a tag) publishes `0.7.0` so a version has a name. It does not
change what is running: `main` rolls on its own `sha-` tag.

**By hand**, if the workflow is unavailable:

```bash
make release IMAGE=ghcr.io/conductionnl/openwoo-provisioner:<tag>   # build + push + verify
```

Then set `newTag` in `kustomization.yaml` yourself. **Build first:** setting it
before the image exists points Argo at a tag it cannot pull.

Two things that will bite you once each:

- **The `HUB_SHA` pin in the Dockerfile is a hard gate.** The build clones
  `ConductionNL/hub` and fails if main has moved past the pin. That is
  deliberate — the handbook content baked into the image should change on
  purpose, with a CHANGELOG entry, not as a side effect of when you happened to
  build. Bump the pin first; the build will not do it for you.
- **ghcr defaults new packages to private.** After the first push, set the
  package to public. A private package means every namespace needs a pull
  secret again, which is exactly the cost this move removes. The registry check
  reports a 401 as "probably still private" rather than "tag missing", because
  the fix is completely different.
- **The bump commit needs to reach `main`.** It is made by
  `github-actions[bot]` and carries `[skip ci]` so it does not retrigger the
  workflow. If branch protection refuses that push, the job fails *after* the
  image is published — so the image exists and only the rollout is stuck. The
  error says exactly that.

To see what is deployed versus what is running:

```bash
./scripts/verify-onboarding.sh --preflight
```

It reads the expected tag from `kustomization.yaml`, so it follows the workflow
automatically instead of carrying its own copy of the version.

## Apply

Preferred: add `argocd-application.example.yaml` (adjusted) to the GitOps repo and
let Argo sync. Manual:

```bash
kubectl apply -k webgui/deploy        # after the Secret exists
```

## Verify

```bash
kubectl -n openwoo-platform rollout status deploy/openwoo-provisioner
curl -sS https://platform.commonground.nu/healthz          # ok (probe path, no auth)
# the form / and /provision require a Google login via Keycloak.
```

Met de egress-policy erbij (networkpolicy-egress.yaml): doorloop ná de rollout
de vier-staps testchecklist uit de kop van die file — assistent-antwoord mét
bronnen, dashboard/Argo-status, login-flow, provisioning-smoke. Faalt er iets,
rol alléén de egress-policy terug (de ingress-ankers blijven staan).

## Platform-assistent (v1, strikt lezend)

`/assistant` draait server-side agent-sessies gegrond in het handboek
(zie `webgui/assistant.py` en de spec `platform-assistant` in techbook).
Deploy-relevant:

- de hub-contentlaag zit **in het image**, gepind op `HUB_SHA` (Dockerfile);
  bumpen = nieuwe sha + CHANGELOG, de build faalt bewust als hub-main
  verder is;
- shallow clones landen in een emptyDir op `/var/cache/docs-mcp`
  (max-age ververst); de gebundelde claude-CLI schrijft state onder
  `$HOME` (tweede emptyDir);
- audit-log gaat als JSONL naar stdout (k8s logs; retentiebesluit is
  taak 1.2 van de change) — zet `ASSISTANT_AUDIT_LOG` voor een extra file;
- tuning via env: `ASSISTANT_MODEL`, `ASSISTANT_RATE_LIMIT`,
  `ASSISTANT_MAX_TURNS`, `ASSISTANT_TIMEOUT`,
  `ASSISTANT_MAX_QUESTION_CHARS`, `ASSISTANT_HEARTBEAT_SECONDS`,
  `ASSISTANT_METRICS_TIMEOUT`, `ASSISTANT_METRICS_MAX_SERIES`
  (defaults in assistant.py; regel: élke limiet is env-tunable,
  niets hardcoded);
- live status: tool `platform_status` leest Argo-sync/health via de
  bestaande SA-RBAC (`rbac-argo.yaml`) — vaste weergaven, antwoorden
  gelabeld als live, aanroepen in het audit-record (change
  add-assistant-live-status fase 1);
- live metrics: tool `metrics_query` bevraagt de in-cluster Prometheus
  (`PROMETHEUS_URL`, default de Service in `monitoring`; expliciet
  gezet in `deployment.yaml`) via een vaste named-query-catalogus —
  het model kiest een naam, de PromQL ligt vast in code; zelfde
  live-labeling en audit (change add-assistant-live-status fase 2).
  Let op: komt de egress-policy terug, dan moet 9090/TCP naar de
  monitoring-namespace in `networkpolicy-egress.yaml`.
