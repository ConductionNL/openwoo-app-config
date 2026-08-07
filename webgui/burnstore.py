#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/burnstore.py — single-use, TTL'd claim tickets for the secret-reveal flow.
#
# An operator mints a ticket for a tenant; the product owner opens the resulting
# link once and sees that tenant's initial Nextcloud admin password. The ticket
# is burned on first claim and expires regardless.
#
# DESIGN: THIS STORE HOLDS NO SECRET MATERIAL.
# The change's design.md proposed storing the password encrypted under
# sha256(token). Python's standard library has no authenticated cipher, and
# hand-rolling one is worse than the problem it solves — so instead the ticket
# records only {tenant, expires_at, requested_by} and the password is read from
# the cluster at claim time. Nothing sensitive is ever at rest here, which is a
# stronger property than "encrypted at rest with a key sitting in the same pod".
#
# What is stored is sha256(token), never the token itself: a reader of the
# ConfigMap cannot reconstruct a working link.
#
# Storage is one ConfigMap in the portal's own namespace, so outstanding links
# survive a pod restart. Losing them would surface to the PO as "link already
# used", which is exactly the wrong signal for a one-time secret.
#
# Writes: one ConfigMap (`secret-reveal-tickets`) in the portal namespace.
# Idempotent: no — minting is intentionally a new ticket each time.
# Requires: in-cluster SA token + CA, RBAC per deploy/rbac-secrets.yaml.
"""Single-use, TTL'd claim tickets (no secret material at rest)."""

import base64
import hashlib
import json
import os
import secrets
import ssl
import time
import urllib.error
import urllib.request

_SA_DIR = os.environ.get("SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
_API = os.environ.get("KUBERNETES_API", "https://kubernetes.default.svc")
_NS = os.environ.get("PORTAL_NAMESPACE", "openwoo-platform")
_CM = os.environ.get("BURNSTORE_CONFIGMAP", "secret-reveal-tickets")

# Env-tunable per house rule: no cap or timeout is hardcoded.
TTL_SECONDS = int(os.environ.get("REVEAL_TTL_SECONDS", "86400"))       # 24h
MAX_TICKETS = int(os.environ.get("REVEAL_MAX_TICKETS", "200"))         # store guard
TOKEN_BYTES = int(os.environ.get("REVEAL_TOKEN_BYTES", "32"))          # 256 bit


class BurnstoreError(Exception):
    """The ticket store could not be read or written."""


class AlreadyMintedError(Exception):
    """Voor deze tenant is al eens een link gemaakt; dat gebeurt maar één keer.

    Draagt het eerdere record (wie, wanneer) zodat de route dat kan tonen —
    "al gedeeld" is een nuttiger antwoord dan een kale weigering.
    """

    def __init__(self, record):
        self.record = record or {}
        super().__init__("er is voor deze omgeving al een wachtwoordlink gemaakt")


def _token_header():
    with open(f"{_SA_DIR}/token", encoding="utf-8") as fh:
        return fh.read().strip()


def _request(method, path, body=None):
    req = urllib.request.Request(f"{_API}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {_token_header()}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context(cafile=f"{_SA_DIR}/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise BurnstoreError(f"{method} {path} -> HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise BurnstoreError(f"cannot reach kube API: {exc.reason}") from None


def _cm_path():
    return f"/api/v1/namespaces/{_NS}/configmaps"


def _load():
    """Return (tickets, resourceVersion). A missing ConfigMap reads as empty."""
    cm = _request("GET", f"{_cm_path()}/{_CM}")
    if cm is None:
        return {}, None
    data = cm.get("data") or {}
    tickets = {}
    for digest, raw in data.items():
        try:
            tickets[digest] = json.loads(raw)
        except (TypeError, ValueError):
            continue      # unparsable row: drop it rather than fail the whole store
    return tickets, (cm.get("metadata") or {}).get("resourceVersion")


def _save(tickets, resource_version):
    """Write the ticket set back, creating the ConfigMap on first use."""
    body = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": _CM, "namespace": _NS},
        "data": {digest: json.dumps(entry, sort_keys=True) for digest, entry in tickets.items()},
    }
    if resource_version is None:
        created = _request("POST", _cm_path(), body)
        if created is None:
            raise BurnstoreError("could not create the ticket ConfigMap")
        return
    body["metadata"]["resourceVersion"] = resource_version
    if _request("PUT", f"{_cm_path()}/{_CM}", body) is None:
        raise BurnstoreError("ticket ConfigMap disappeared while writing")


def read_admin_password(tenant):
    """Read a tenant's initial Nextcloud admin password from the cluster.

    Per Nextcloud-base/docs/SECRETS.md: every tenant ends up with a Secret named
    `nextcloud-secrets` in its namespace, and the namespace is the BARE tenant
    name (`straatje-accept`) — `nc-<tenant>` is the Argo application name, not a
    namespace. The admin password lives under `nextcloud-password`, explicitly
    not `admin-password`. Both mechanisms (create-tenant-secret.sh for existing
    tenants, ESO for managed ones) produce the same shape, so this path does not
    depend on which one created it.

    Returns the password, or None when the Secret or the key is absent.

    NOTE ON BLAST RADIUS: `nextcloud-secrets` also holds S3, database and Redis
    credentials, and Kubernetes RBAC cannot authorise per key. The RBAC grant is
    therefore wider than this feature needs, which makes THIS FUNCTION the real
    boundary: it returns exactly one key and never surfaces, logs or returns the
    rest of the Secret.
    """
    secret = _request("GET", f"/api/v1/namespaces/{tenant}/secrets/nextcloud-secrets")
    if secret is None:
        return None
    encoded = (secret.get("data") or {}).get("nextcloud-password")
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise BurnstoreError(f"nextcloud-password for '{tenant}' is not valid base64/utf-8")


def digest(token):
    """sha256 of the token — the only form that is ever stored."""
    return hashlib.sha256(token.encode()).hexdigest()


# Eén onthulling per tenant, ooit. Het initiële adminwachtwoord is een
# overdracht, geen opzoekfunctie: na de eerste login hoort de ontvanger het te
# wijzigen, en elke volgende link deelt een wachtwoord dat er niet meer toe doet
# of juist nog wél — allebei redenen om het niet nog eens rond te sturen.
#
# Vóór deze grens maakte elke klik een nieuwe geldige link; vier stuks in een
# minuut tijdens de dry-run van 2026-08-07, waarvan twee voor een productie-
# tenant. De link zelf was eenmalig, de knop niet.
#
# Sleutel in dezelfde ConfigMap, met een prefix zodat hij niet met een ticket
# te verwarren is. Kwijtgeraakte link? Dan leest een operator het secret met
# kubectl — dat is bewust omslachtiger dan opnieuw delen.
_MINTED_PREFIX = "minted-"


def minted_record(tenant):
    """Wanneer en door wie er voor `tenant` al een link is gemaakt, of None."""
    tickets, _version = _load()
    return tickets.get(_MINTED_PREFIX + tenant)


def minted_tenants():
    """Namen van omgevingen die hun wachtwoordlink al gehad hebben."""
    tickets, _version = _load()
    return sorted(d[len(_MINTED_PREFIX):] for d in tickets if d.startswith(_MINTED_PREFIX))


def _prune(tickets, now):
    """Verlopen tickets eruit. Merktekens (minted-<tenant>) blijven: die hebben
    geen expires_at en horen niet te verlopen — ze zeggen "dit is ooit gedeeld"."""
    return {d: e for d, e in tickets.items()
            if d.startswith(_MINTED_PREFIX) or float(e.get("expires_at", 0)) > now}


def mint(tenant, requested_by, ttl=None, now=None):
    """Create a ticket for `tenant` and return the raw token (shown once, to the
    operator). Expired tickets are pruned on the way through.

    Raises BurnstoreError when the store is full — a runaway mint loop should
    fail loudly rather than grow an unbounded ConfigMap.
    """
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(TOKEN_BYTES)
    tickets, version = _load()
    # Eén keer per tenant. Het merkteken verloopt niet mee met de tickets.
    already = tickets.get(_MINTED_PREFIX + tenant)
    if already:
        raise AlreadyMintedError(already)
    tickets = _prune(tickets, now)
    # Alleen echte tickets tellen: merktekens zijn permanent en zouden de cap
    # anders langzaam volvreten tot niemand meer een link kan maken.
    open_tickets = sum(1 for d in tickets if not d.startswith(_MINTED_PREFIX))
    if open_tickets >= MAX_TICKETS:
        raise BurnstoreError(
            f"ticket store full ({open_tickets}/{MAX_TICKETS}); raise REVEAL_MAX_TICKETS "
            f"or wait for tickets to expire")
    tickets[digest(token)] = {
        "tenant": tenant,
        "requested_by": requested_by,
        "expires_at": now + (TTL_SECONDS if ttl is None else ttl),
    }
    # Het merkteken heeft geen expires_at, dus _prune() raakt het niet.
    tickets[_MINTED_PREFIX + tenant] = {
        "tenant": tenant,
        "requested_by": requested_by,
        "minted_at": now,
    }
    _save(tickets, version)
    return token


def claim(token, now=None):
    """Burn the ticket for `token` and return its entry, or None.

    The ticket is removed BEFORE the caller does anything with the result: a
    crash between here and the response must not leave a second read possible.
    An expired ticket is removed and reported as None, so "expired" and "already
    used" are indistinguishable from the outside — a probe learns nothing.
    """
    now = time.time() if now is None else now
    tickets, version = _load()
    entry = tickets.pop(digest(token), None)
    before = len(tickets)
    tickets = _prune(tickets, now)
    if entry is not None or len(tickets) != before:
        _save(tickets, version)
    if entry is None or float(entry.get("expires_at", 0)) <= now:
        return None
    return entry
