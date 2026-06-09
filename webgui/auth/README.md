# Auth (Phase 2): oauth2-proxy → Keycloak → Google

The provisioning web GUI (`webgui/server.py`) has **no login of its own**. It is
fronted by [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/), which
authenticates the operator against **Keycloak** (realm `commonground`,
`https://iam.commonground.nu`). Keycloak in turn **brokers Google** as an
identity provider, so operators log in with their Google account but the GUI only
ever integrates with Keycloak (OIDC).

```
browser ──▶ oauth2-proxy ──OIDC──▶ Keycloak ──brokers──▶ Google
                  │  (sets X-Forwarded-Email / X-Forwarded-User)
                  ▼
            Flask app (127.0.0.1:8081)  ── fails closed if the header is absent
```

## Trust model — why the header is safe to believe

The app trusts `X-Forwarded-Email` **only because oauth2-proxy is the sole
ingress**. Two things enforce that:

1. **Topology** — the Flask app binds `127.0.0.1` (or is reachable only from the
   proxy via a NetworkPolicy); nothing else can reach it to spoof the header.
2. **Fail closed** — `server.py` runs with `REQUIRE_AUTH=true` by default and
   returns `403` on any request (except `/healthz`) that arrives without an
   identity header. So a misconfigured ingress degrades to "locked", not "open".

If you run the app *without* a proxy for local dev, set `REQUIRE_AUTH=false`.

## What lives where

| Piece | Location |
|---|---|
| oauth2-proxy config | `webgui/auth/oauth2-proxy.cfg` (this repo) |
| Keycloak client `openwoo-provisioner` | KeyCloak repo, `realm-commonground.yaml` |
| Google identity provider | KeyCloak repo, `realm-commonground.yaml` |
| Secrets (client + cookie) | env / cluster Secret — **never in Git** |

## Keycloak side (KeyCloak repo)

Add an OIDC client `openwoo-provisioner` to the `commonground` realm (confidential,
standard flow), with the oauth2-proxy callback as redirect URI:

- **Redirect URI:** `https://<gui-host>/oauth2/callback`
- **Web origin:** `https://<gui-host>`
- **Scopes:** `openid profile email`

For Google login, the realm needs a Google **identity provider** (`alias: google`,
`providerId: google`). The Google OAuth client id/secret come from the Google
Cloud console and are injected as a secret (SOPS / External Secrets), not Git —
matching the KeyCloak repo's existing convention.

> The realm change is a prod-path edit in the KeyCloak repo; see that repo's
> `docs/REALMS.md` for the add-client + kustomization + secret steps.

## Required secrets at runtime

| Env var | What |
|---|---|
| `OAUTH2_PROXY_CLIENT_SECRET` | the Keycloak `openwoo-provisioner` client secret |
| `OAUTH2_PROXY_COOKIE_SECRET` | 32-byte random, base64 (`openssl rand -base64 32`) |

## Local smoke test (no Google, no cluster)

You can verify the **fail-closed** behaviour without any proxy:

```bash
# default (REQUIRE_AUTH on): no identity header -> 403
curl -i http://127.0.0.1:8081/                       # 403
curl -i -H 'X-Forwarded-Email: you@conduction.nl' \
        http://127.0.0.1:8081/                        # 200

# dev mode: auth disabled
REQUIRE_AUTH=false python3 webgui/server.py
```

Full end-to-end (real Google login) requires the Keycloak client + oauth2-proxy
running in front — wired up in Phase 3 (deploy).
