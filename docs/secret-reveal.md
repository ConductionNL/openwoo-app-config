---
last_reviewed: 2026-08-07
owner: info@conduction.nl
---

# Handing over a tenant's initial admin password

A newly created tenant has a generated Nextcloud admin password sitting in
its `nextcloud-secrets` Secret. Eraan komen betekende een devops-persoon die
`kubectl get secret … | base64 -d` draaide en het resultaat in een chat of
mail plakte — precies de afhankelijkheid die deze levenscyclus wil weghalen,
plus een kopie van een gemeentelijk adminwachtwoord in iemands berichten.

The portal replaces that with a link that works exactly once.

## The flow

1. Een **operator** klikt **wachtwoord inzien** bij de omgeving op het
   dashboard. Dat kan **één keer per omgeving, ooit**: daarna staat er
   "wachtwoord ingezien" en wijst een tweede poging af met 409, inclusief
   wie hem eerder maakte en wanneer.
2. De link verschijnt klikbaar én kopieerbaar. Zelf openen kan, doorsturen
   ook — het portaal verstuurt niets.
3. Openen toont het wachtwoord één keer, op een pagina zonder JavaScript.
4. Elke tweede opvraging — ook door dezelfde persoon — geeft 404.

Kwijtgeraakt vóór gebruik? Dan leest een operator het secret met `kubectl`.
Bewust omslachtiger dan opnieuw kunnen delen. Een verlopen link geeft
hetzelfde 404 als een gebruikte: die twee zijn expres niet te onderscheiden.

## Wie de link mag openen

**Alleen een ingelogde Conduction-medewerker.** oauth2-proxy vraagt om een
login met een adres uit `email_domains` voor élke route, ook `/reveal/`.

Dat is een besluit van 2026-08-07 en het wijkt af van de oorspronkelijke
opzet in de change: die ging uit van een product owner zonder account, met
het token als enige poort. Die aanname is vervallen. Het token blijft
eenmalig en kortlevend, maar het is nu een tweede slot achter de login in
plaats van het enige slot.

Praktisch gevolg: het wachtwoord bij een externe partij krijgen gaat via een
kanaal buiten dit portaal. De link zelf werkt daar niet.

De rest van deze paragraaf beschrijft waarom het token ook op zichzelf
deugt — dat blijft gelden, en het is nu de tweede verdedigingslinie.

## Waarom het token ook zonder die login zou houden

Het token van 256 bits is op zichzelf al een credential. Wat dat eerlijk
houdt:

- **Minten is operator-gated.** Alleen een ingelogde operator kan een token
  laten ontstaan, en dat kan één keer per omgeving.
- **The store holds no secret material.** A ticket records
  `{tenant, expires_at, requested_by}` — the password is read from the
  cluster at claim time. Reading the store gives an attacker nothing.
- **Only `sha256(token)` is stored,** never the token, so the stored form
  cannot be replayed as a link.
- **The ticket is burned before the password is fetched.** A crash or a
  failed read still consumes the link; it can never be retried into a
  second disclosure.
- **The value is never logged.** The audit line records who minted it, for
  which tenant, and that a claim happened — never the password.

### Deviation from the change's design.md

`design.md` proposed storing the password encrypted at rest under the token
digest. The implementation stores no password at all instead. Python's
standard library has no authenticated cipher, and hand-rolling one would be
worse than the problem; "never stored" is also a stronger property than
"encrypted with a key that lives in the same pod".

## What it reads, and the honest limit

Per
[Nextcloud-base SECRETS.md](https://github.com/ConductionNL/Nextcloud-base/blob/main/docs/SECRETS.md),
every tenant ends up with a Secret `nextcloud-secrets` in its namespace —
the **bare tenant name** (`straatje-accept`; `nc-<tenant>` is the Argo
application, not a namespace). The admin password is under
`nextcloud-password`, not `admin-password`. Both mechanisms produce that
shape, so this flow does not care whether the Secret came from
`create-tenant-secret.sh` or from ESO.

`nextcloud-secrets` also holds S3, database and Redis credentials, and
Kubernetes RBAC cannot authorise per key inside a Secret. The RBAC grant
(`get`, `resourceNames: [nextcloud-secrets]`, no `list`/`watch`) is
therefore wider than the feature needs. **The code is the boundary:**
`burnstore.read_admin_password()` returns exactly one key. Narrowing the
grant further would require Nextcloud-base to split the admin password into
its own Secret.

Sinds de certificaat-upload heeft het portaal daarnaast `create`/`update` op
Secrets, en dát is niet op naam te beperken — zie
[custom-domain-cert.md](custom-domain-cert.md) en `deploy/rbac-secrets.yaml`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `REVEAL_ENABLED` | `false` | Master switch. Off means both routes 404. |
| `REVEAL_TTL_SECONDS` | `86400` | Link lifetime (24h). |
| `REVEAL_MAX_TICKETS` | `200` | Outstanding-ticket guard; minting fails loudly above it. |
| `REVEAL_TOKEN_BYTES` | `32` | Token entropy (256 bit). |
| `PORTAL_NAMESPACE` | `openwoo-platform` | Where the ticket ConfigMap lives. |
| `BURNSTORE_CONFIGMAP` | `secret-reveal-tickets` | Ticket ConfigMap name. |

The flag is off by default because the route reads a tenant Secret: it
stays dark until someone deliberately turns it on for a deployment.

## Operator notes

- **Minting fails fast** when the tenant has no readable
  `nextcloud-password`, so you find out immediately instead of the product
  owner finding out at the link.
- **Tell the recipient it is single-use.** The page says so, but a link
  that a mail client pre-fetches is a link already burned. If that turns
  out to bite in practice, that is an argument for a click-through step,
  not for making the link reusable.
- **Rotation is not part of this.** ESO generates once and does not rotate
  (`refreshInterval: "0"`); the product owner should change the password
  after first login.
