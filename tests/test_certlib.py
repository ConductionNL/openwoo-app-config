# SPDX-License-Identifier: EUPL-1.2
"""Validatie van een geüpload TLS-paar. Geen cluster nodig; certs in-memory."""

import datetime
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import certlib  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def _pair(host="open.almere.nl", days=90, sans=None, not_before_days=-1):
    """Zelfondertekend paar; geeft (cert_pem, key_pem, key)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    builder = (x509.CertificateBuilder()
               .subject_name(name).issuer_name(name)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now + datetime.timedelta(days=not_before_days))
               .not_valid_after(now + datetime.timedelta(days=days)))
    names = [x509.DNSName(h) for h in (sans if sans is not None else [host])]
    if names:
        builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM,
                              serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()),
            key)


def test_valid_pair_is_accepted():
    cert, key, _ = _pair()
    got = certlib.validate(cert, key, "open.almere.nl")
    assert got["days_remaining"] > 80
    assert got["hosts"] == ["open.almere.nl"]
    # de samenvatting mag geen sleutelmateriaal bevatten
    assert "BEGIN" not in str(got)


def test_key_that_does_not_belong_is_refused():
    """De klassieke kopieerfout: cert van de klant, key van iets anders. Zonder
    deze check levert dat een Ingress op waar geen browser mee overweg kan."""
    cert, _, _ = _pair()
    _, other_key, _ = _pair(host="ander.nl")
    with pytest.raises(certlib.CertError, match="hoort niet bij"):
        certlib.validate(cert, other_key, "open.almere.nl")


def test_certificate_for_another_host_is_refused():
    cert, key, _ = _pair(host="ander.nl")
    with pytest.raises(certlib.CertError, match="dekt open.almere.nl niet"):
        certlib.validate(cert, key, "open.almere.nl")


def test_wildcard_covers_one_label():
    cert, key, _ = _pair(host="*.almere.nl", sans=["*.almere.nl"])
    assert certlib.validate(cert, key, "open.almere.nl")
    with pytest.raises(certlib.CertError):
        certlib.validate(cert, key, "diep.open.almere.nl")


def test_expired_certificate_is_refused():
    cert, key, _ = _pair(days=-1, not_before_days=-10)
    with pytest.raises(certlib.CertError, match="verlopen"):
        certlib.validate(cert, key, "open.almere.nl")


def test_almost_expired_is_refused_as_probably_wrong_bundle(monkeypatch):
    """Een bundel die morgen verloopt is bijna altijd de verkeerde."""
    monkeypatch.setattr(certlib, "MIN_REMAINING_DAYS", 30)
    cert, key, _ = _pair(days=5)
    with pytest.raises(certlib.CertError, match="verloopt over"):
        certlib.validate(cert, key, "open.almere.nl")


def test_garbage_input_is_refused_cleanly():
    with pytest.raises(certlib.CertError, match="geen geldige PEM"):
        certlib.validate(b"dit is geen pem", b"dit ook niet", "open.almere.nl")


def test_oversized_upload_is_refused(monkeypatch):
    monkeypatch.setattr(certlib, "MAX_UPLOAD_BYTES", 10)
    cert, key, _ = _pair()
    with pytest.raises(certlib.CertError, match="te groot"):
        certlib.validate(cert, key, "open.almere.nl")


def test_secret_is_written_under_the_derived_name(monkeypatch):
    """De naam komt van de aanroeper (afgeleid uit de host), nooit uit het
    verzoek — anders kan een upload een ander secret overschrijven."""
    seen = {}

    def fake_request(method, path, body=None):
        seen.setdefault("calls", []).append((method, path))
        if method == "GET":
            return None                       # bestaat nog niet
        seen["body"] = body
        return {"ok": True}

    monkeypatch.setattr(certlib, "_request", fake_request)
    cert, key, _ = _pair()
    action = certlib.write_secret("almere-accept", "open-almere-nl-tls", cert, key)
    assert action == "created"
    assert seen["body"]["metadata"]["name"] == "open-almere-nl-tls"
    assert seen["body"]["type"] == "kubernetes.io/tls"
    assert set(seen["body"]["data"]) == {"tls.crt", "tls.key"}


def test_existing_secret_is_replaced(monkeypatch):
    monkeypatch.setattr(certlib, "_request",
                        lambda m, p, b=None: {"metadata": {}} if m == "GET" else {"ok": True})
    cert, key, _ = _pair()
    assert certlib.write_secret("a-accept", "n-tls", cert, key) == "replaced"
