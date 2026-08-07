---
last_reviewed: 2026-08-07
owner: info@conduction.nl
---

# A tenant on its own domain: the certificate

Most WOO frontends live on `*.openwoo.app` and are covered by one shared
wildcard certificate — nothing to do. This page is for the exception: a
tenant whose frontend answers on the organisation's own domain, for
example `open.almere.nl`.

Two things have to be true for that to work: the tenant file must say
which Secret holds the certificate, and that Secret must exist. The first
happens in git; the second never does.

## Why the certificate is not in git

The portal opens pull requests. It holds no secrets and touches nothing in
the cluster except the two narrowly-scoped reads it needs. Accepting a
PEM + private key through a web form would give a customer-facing app the
right to write arbitrary Secrets — a large new privilege for something
that happens a handful of times a year.

So the tenant file carries the Secret's **name**, and the bytes travel
out of band. The PR is reviewable without anyone seeing key material, and
git never becomes a place where a private key might have been.

## What the form produces

Fill in a custom host on the create-tenant form and pick a certificate
source. The resulting tenant file gains:

```yaml
  frontend:
    host: open.almere.nl
    tls:
      secretName: open-almere-nl-tls
      issuer: none
```

The Secret name is derived from the host (dots become dashes, `-tls`
suffix), matching what the fleet already does — no new convention.

`issuer` is the choice:

| Value | Meaning | Who creates the Secret |
|---|---|---|
| `none` (default) | Bring your own. No cert-manager annotation, no `Certificate` object, so nothing can overwrite a customer-supplied certificate. | an operator, out of band (below) |
| `letsencrypt-prod` | cert-manager issues per host over HTTP-01. | cert-manager, automatically |

**Why `none` is the default.** The two failure modes are not symmetric.
A missing certificate is loud — the browser complains and someone fixes
it within minutes. A wrongly-issued one is quiet: Let's Encrypt overwrites
a paid certificate the organisation bought, and nobody notices until the
customer does. Choosing Let's Encrypt is one click; choosing it by
accident should not be possible.

If the organisation has no certificate of its own and the domain resolves
to this cluster, pick `letsencrypt-prod` and you are done — the rest of
this page does not apply.

## Landing a bring-your-own certificate

Order matters. The namespace must exist before the Secret can go into it,
and the namespace is created by the merge.

1. **Merge the tenant PR.** Argo creates the namespace and the frontend.
   Until step 3, the site answers with the wrong certificate — expected.
2. **Get the bundle** from the organisation. It arrives in whatever format
   their CA produced: PFX, a PEM bundle, separate files, PKCS#7, a zip.
3. **Write the Secret** into the tenant namespace (the *bare* tenant name):

       certswap plan  k8s <bundle> --namespace <tenant> --secret <name>
       certswap apply k8s <bundle> --namespace <tenant> --secret <name> \
         --argocd-app nc-<tenant>

   `certswap` normalises the input formats, checks the chain, and does an
   ArgoCD-aware in-place swap. It is an **external tool**, not a platform
   component — so the dependency-free path is equally supported:

       kubectl create secret tls <name> \
         --namespace <tenant> --cert=fullchain.pem --key=privkey.pem

   Use the `secretName` from the tenant file verbatim.
4. **Verify** the site serves the right certificate:

       openssl s_client -connect open.almere.nl:443 -servername open.almere.nl \
         </dev/null 2>/dev/null | openssl x509 -noout -issuer -subject -dates

5. **Record the expiry date and the owner.** In the tenant file, as a
   comment next to the block — that is where the next person will look:

       tls:
         secretName: open-almere-nl-tls
         # Paid certSIGN cert, expires 2027-02-14, seeded by hand.
         # Renewal: contact <team> at the organisation.
         issuer: none

## The part that will bite you: renewal

**A bring-your-own certificate expires without warning.** Monitoring's
`CertificateExpiringSoon` alert fires on
`certmanager_certificate_expiration_timestamp_seconds` — a metric
cert-manager only produces for `Certificate` objects. With `issuer: none`
there is no `Certificate`, so **the alert does not cover these**. The
comment in step 5 is currently the only tracking that exists.

Closing that gap means a probe that reads the certificate out of the
Secret (or off the live endpoint) rather than out of cert-manager. That is
a change for the monitoring repo, not this one; it is recorded as a
follow-up rather than quietly assumed to be handled.

Renewing is step 3 again with the new bundle, followed by step 4. The
frontend picks up the new Secret without a restart.

## Troubleshooting

**The site serves the wildcard or a Kubernetes default certificate.** The
Secret does not exist yet, or its name does not match `secretName`. Compare
them literally:

    kubectl get secret -n <tenant> <name>

**A customer certificate was replaced by a Let's Encrypt one.** The tenant
file has an `issuer` other than `none`, so cert-manager took ownership of
the Secret. Set `issuer: none`, merge, delete the `Certificate` object, and
re-seed the customer certificate. This is the exact failure the default
guards against.

**`certswap` is not installed.** Use the `kubectl create secret tls`
fallback in step 3. Nothing about this runbook depends on having it.
