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
| `networkpolicy.yaml` | ingress only from ingress-nginx; egress DNS + 443 |
| `oauth2-proxy.cfg` | proxy config (Keycloak OIDC; → ConfigMap via kustomize) |
| `secret.example.yaml` | **template** — real Secret created out-of-band |
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

## Build & push the image

```bash
make image IMAGE=<registry>/openwoo-provisioner:<tag>     # docker build
make push  IMAGE=<registry>/openwoo-provisioner:<tag>
# then point kustomize at it:
cd webgui/deploy && kustomize edit set image \
  ghcr.io/conductionnl/openwoo-provisioner=<registry>/openwoo-provisioner:<tag>
```

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
