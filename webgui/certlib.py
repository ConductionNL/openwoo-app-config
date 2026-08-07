#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/certlib.py — valideert een geüpload TLS-paar en schrijft het als Secret.
#
# Doet in het portaal wat `certswap` buiten het portaal deed: een bundel
# controleren en in de tenant-namespace zetten. Het verschil met de oude route
# is niet de handeling maar wie hem uitvoert, en dat verandert de blootstelling:
# privésleutelmateriaal gaat nu door een webproces heen.
#
# Daarom is dit bestand streng op drie punten, en die zijn geen decoratie:
#
#   1. VALIDEREN VOOR SCHRIJVEN. Een sleutel die niet bij het certificaat hoort,
#      of een certificaat dat de host niet dekt, levert een Ingress op die
#      stilzwijgend het verkeerde serveert. Beter een nette fout dan een tenant
#      die "het doet" met andermans certificaat.
#   2. NIETS LOGGEN, NIETS BEWAREN. Geen sleutel, geen certificaat, geen
#      fingerprint in een logregel. Alleen het feit dat er geschreven is, en
#      voor welke tenant. Vandaag ging het mis met een reveal-token in de
#      access-log; dit is dezelfde klasse.
#   3. ALLEEN DE AFGELEIDE NAAM. Het secret heet wat tenants.tls_secret_name()
#      uit de host afleidt. Zo kan een geüpload bestand nooit een ander secret
#      overschrijven, ook niet als iemand de naam probeert mee te sturen.
#
# Writes: één Secret per aanroep, in de namespace van de tenant.
# Idempotent: ja — bestaat het secret al, dan wordt het vervangen.
# Requires: cryptography (validatie), in-cluster SA-token + CA, RBAC per
#   deploy/rbac-secrets.yaml.
"""Validatie en plaatsing van een door de klant geleverd TLS-certificaat."""

import base64
import datetime
import json
import os
import ssl
import urllib.error
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

_SA_DIR = os.environ.get("SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
_API = os.environ.get("KUBERNETES_API", "https://kubernetes.default.svc")

# Een certificaat dat al bijna verlopen is, is bijna altijd de verkeerde bundel.
MIN_REMAINING_DAYS = int(os.environ.get("CERT_MIN_REMAINING_DAYS", "1"))
MAX_UPLOAD_BYTES = int(os.environ.get("CERT_MAX_UPLOAD_BYTES", "1048576"))


class CertError(Exception):
    """De upload deugt niet, of het schrijven mislukte. Bericht is voor de operator."""


def _load_cert_chain(pem):
    certs = x509.load_pem_x509_certificates(pem)
    if not certs:
        raise CertError("geen certificaat gevonden in het bestand (verwacht PEM)")
    return certs


def _public_numbers(key):
    """Vergelijkbare representatie van de publieke helft, voor beide sleuteltypes."""
    pub = key.public_key() if hasattr(key, "public_key") else key
    return pub.public_bytes(serialization.Encoding.DER,
                            serialization.PublicFormat.SubjectPublicKeyInfo)


def validate(cert_pem, key_pem, host, now=None):
    """Controleer de bundel tegen `host`. Geeft een samenvatting terug of raise.

    De samenvatting bevat expres géén sleutelmateriaal: alleen wat een operator
    moet zien om te geloven dat de juiste bundel is geland.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if len(cert_pem) > MAX_UPLOAD_BYTES or len(key_pem) > MAX_UPLOAD_BYTES:
        raise CertError("bestand te groot")

    try:
        chain = _load_cert_chain(cert_pem)
    except CertError:
        raise
    except Exception:
        raise CertError("certificaat is geen geldige PEM") from None
    leaf = chain[0]

    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except TypeError:
        raise CertError("de sleutel is met een wachtwoord beveiligd; lever hem "
                        "onversleuteld aan") from None
    except Exception:
        raise CertError("sleutel is geen geldige PEM") from None
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise CertError("sleuteltype wordt niet ondersteund (verwacht RSA of EC)")

    # 1. Horen ze bij elkaar? Zo niet, dan serveert de Ingress straks iets waar
    #    geen browser mee overweg kan.
    if _public_numbers(key) != _public_numbers(leaf):
        raise CertError("de sleutel hoort niet bij dit certificaat")

    # 2. Geldigheid.
    not_before = leaf.not_valid_before_utc
    not_after = leaf.not_valid_after_utc
    if now < not_before:
        raise CertError(f"certificaat is nog niet geldig (vanaf {not_before:%Y-%m-%d})")
    if now > not_after:
        raise CertError(f"certificaat is verlopen op {not_after:%Y-%m-%d}")
    remaining = (not_after - now).days
    if remaining < MIN_REMAINING_DAYS:
        raise CertError(f"certificaat verloopt over {remaining} dag(en) — "
                        f"waarschijnlijk de verkeerde bundel")

    # 3. Dekt het de host? Een certificaat voor een ander domein is de
    #    klassieke kopieerfout en levert een site op die niet vertrouwd wordt.
    names = _san_names(leaf)
    if not _covers(names, host):
        raise CertError(f"certificaat dekt {host} niet (het geldt voor: "
                        f"{', '.join(sorted(names)) or 'geen SAN-namen'})")

    return {
        "subject": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "not_after": not_after.strftime("%Y-%m-%d"),
        "days_remaining": remaining,
        "hosts": sorted(names),
        "chain_length": len(chain),
        "fingerprint_sha256": leaf.fingerprint(hashes.SHA256()).hex()[:16],
    }


def _san_names(cert):
    names = set()
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names.update(ext.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    # CN telt alleen mee als er geen SAN is — moderne clients negeren hem anders.
    if not names:
        for attr in cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME):
            names.add(str(attr.value))
    return names


def _covers(names, host):
    host = host.lower().rstrip(".")
    for n in (x.lower().rstrip(".") for x in names):
        if n == host:
            return True
        if n.startswith("*.") and host.split(".", 1)[-1] == n[2:]:
            return True
    return False


def _request(method, path, body=None):
    with open(f"{_SA_DIR}/token", encoding="utf-8") as fh:
        token = fh.read().strip()
    req = urllib.request.Request(
        f"{_API}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context(cafile=f"{_SA_DIR}/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read().decode()
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CertError(f"kube API gaf HTTP {exc.code} op {method} {path}") from None
    except urllib.error.URLError as exc:
        raise CertError(f"kube API onbereikbaar: {exc.reason}") from None


def write_secret(tenant, name, cert_pem, key_pem):
    """Schrijf (of vervang) het TLS-secret `name` in namespace `tenant`.

    De naam komt van de aanroeper en die leidt hem af uit de host — hier wordt
    hij niet uit gebruikersinvoer overgenomen.
    """
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "kubernetes.io/tls",
        "metadata": {
            "name": name,
            "namespace": tenant,
            # Herkenbaar in een audit: dit secret is door het portaal geplaatst,
            # niet door cert-manager.
            "labels": {"app.kubernetes.io/managed-by": "openwoo-provisioner"},
        },
        "data": {
            "tls.crt": base64.b64encode(cert_pem).decode("ascii"),
            "tls.key": base64.b64encode(key_pem).decode("ascii"),
        },
    }
    path = f"/api/v1/namespaces/{tenant}/secrets"
    existing = _request("GET", f"{path}/{name}")
    if existing is None:
        if _request("POST", path, body) is None:
            raise CertError("aanmaken van het secret mislukte")
        return "created"
    if _request("PUT", f"{path}/{name}", body) is None:
        raise CertError("bijwerken van het secret mislukte")
    return "replaced"
