# SPDX-License-Identifier: EUPL-1.2
"""Unit tests for the single-use reveal tickets (no cluster needed)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webgui"))

import burnstore  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    """In-memory stand-in for the ticket ConfigMap."""
    state = {"tickets": {}, "version": "1"}

    monkeypatch.setattr(burnstore, "_load", lambda: (dict(state["tickets"]), state["version"]))

    def fake_save(tickets, version):
        state["tickets"] = dict(tickets)

    monkeypatch.setattr(burnstore, "_save", fake_save)
    return state


def _live(store):
    """Tickets zonder de permanente merktekens."""
    return {d: e for d, e in store["tickets"].items() if not d.startswith("minted-")}


def test_mint_returns_a_token_and_stores_only_its_digest(store):
    token = burnstore.mint("almere-accept", "op@conduction.nl")
    assert len(token) > 30
    assert list(_live(store)) == [burnstore.digest(token)]
    # naast het ticket staat er een permanent merkteken voor deze tenant
    assert "minted-almere-accept" in store["tickets"]
    # the raw token must be nowhere in the stored state
    assert token not in str(store["tickets"])


def test_stored_ticket_holds_no_secret_material(store):
    token = burnstore.mint("almere-accept", "op@conduction.nl")
    entry = store["tickets"][burnstore.digest(token)]
    assert set(entry) == {"tenant", "requested_by", "expires_at"}


def test_claim_returns_the_entry_once(store):
    token = burnstore.mint("almere-accept", "op@conduction.nl")
    entry = burnstore.claim(token)
    assert entry["tenant"] == "almere-accept"
    assert burnstore.claim(token) is None          # burned
    assert _live(store) == {}                      # merkteken blijft, ticket weg


def test_claim_of_an_unknown_token_is_none(store):
    assert burnstore.claim("nope") is None


def test_expired_ticket_is_not_claimable(store):
    token = burnstore.mint("almere-accept", "op@conduction.nl", ttl=10, now=1000)
    assert burnstore.claim(token, now=1011) is None
    assert _live(store) == {}                      # and it is swept


def test_claim_burns_before_anything_else_can_fail(store):
    """Even a ticket whose tenant no longer resolves must be gone afterwards."""
    token = burnstore.mint("weg-accept", "op@conduction.nl", ttl=10, now=1000)
    burnstore.claim(token, now=1001)
    assert _live(store) == {}


def test_mint_prunes_expired_tickets(store):
    old = burnstore.mint("oud-accept", "op@conduction.nl", ttl=10, now=1000)
    burnstore.mint("nieuw-accept", "op@conduction.nl", ttl=10, now=2000)
    assert burnstore.digest(old) not in store["tickets"]


def test_mint_refuses_when_the_store_is_full(store, monkeypatch):
    monkeypatch.setattr(burnstore, "MAX_TICKETS", 2)
    burnstore.mint("a-accept", "op@conduction.nl")
    burnstore.mint("b-accept", "op@conduction.nl")
    with pytest.raises(burnstore.BurnstoreError, match="full"):
        burnstore.mint("c-accept", "op@conduction.nl")


def test_tokens_are_unique(store):
    tokens = {burnstore.mint(f"t{i}-accept", "op@conduction.nl") for i in range(20)}
    assert len(tokens) == 20


def test_load_drops_unparsable_rows_instead_of_failing(monkeypatch):
    monkeypatch.setattr(burnstore, "_request",
                        lambda *a, **kw: {"metadata": {"resourceVersion": "9"},
                                          "data": {"good": '{"tenant":"a-accept"}',
                                                   "bad": "not json"}})
    tickets, version = burnstore._load()
    assert list(tickets) == ["good"] and version == "9"


def test_missing_configmap_reads_as_empty(monkeypatch):
    monkeypatch.setattr(burnstore, "_request", lambda *a, **kw: None)
    assert burnstore._load() == ({}, None)


# --- reading the tenant secret ---


def test_read_admin_password_decodes_the_right_key(monkeypatch):
    captured = {}

    def fake_request(method, path, body=None):
        captured["path"] = path
        return {"data": {"nextcloud-password": "aGVpbGlnYm9vbnRqZQ==",
                         "s3-secret-key": "c2hvdWxkLW5vdC1sZWFr"}}

    monkeypatch.setattr(burnstore, "_request", fake_request)
    got = burnstore.read_admin_password("almere-accept")
    assert got == "heiligboontje"
    # namespace is the BARE tenant name, secret name per SECRETS.md
    assert captured["path"] == "/api/v1/namespaces/almere-accept/secrets/nextcloud-secrets"


def test_read_admin_password_returns_only_that_key(monkeypatch):
    monkeypatch.setattr(burnstore, "_request",
                        lambda *a, **kw: {"data": {"nextcloud-password": "cHc=",
                                                   "db-password": "c2VjcmV0"}})
    assert burnstore.read_admin_password("a-accept") == "pw"


def test_read_admin_password_missing_secret_is_none(monkeypatch):
    monkeypatch.setattr(burnstore, "_request", lambda *a, **kw: None)
    assert burnstore.read_admin_password("a-accept") is None


def test_read_admin_password_missing_key_is_none(monkeypatch):
    monkeypatch.setattr(burnstore, "_request",
                        lambda *a, **kw: {"data": {"db-password": "c2VjcmV0"}})
    assert burnstore.read_admin_password("a-accept") is None


def test_one_link_per_tenant_ever(store):
    """Eén overdracht per omgeving. Vóór deze grens maakte elke klik een nieuwe
    geldige link — vier stuks in een minuut tijdens de dry-run van 2026-08-07,
    waarvan twee voor een productie-tenant."""
    burnstore.mint("almere-accept", "op@conduction.nl")
    with pytest.raises(burnstore.AlreadyMintedError) as exc:
        burnstore.mint("almere-accept", "iemand@conduction.nl")
    assert exc.value.record["requested_by"] == "op@conduction.nl"


def test_claiming_does_not_reopen_the_door(store):
    """Ook ná gebruik blijft het bij één keer: het merkteken verloopt niet."""
    token = burnstore.mint("almere-accept", "op@conduction.nl")
    assert burnstore.claim(token) is not None
    with pytest.raises(burnstore.AlreadyMintedError):
        burnstore.mint("almere-accept", "op@conduction.nl")


def test_marker_survives_pruning(store):
    burnstore.mint("almere-accept", "op@conduction.nl", ttl=10, now=1000)
    burnstore.mint("bergen-accept", "op@conduction.nl", ttl=10, now=9999)
    assert "minted-almere-accept" in store["tickets"]      # ticket weg, merkteken blijft
    assert burnstore.minted_tenants() == ["almere-accept", "bergen-accept"]


def test_markers_do_not_eat_the_ticket_cap(store, monkeypatch):
    monkeypatch.setattr(burnstore, "MAX_TICKETS", 2)
    burnstore.mint("a-accept", "op@conduction.nl")
    burnstore.mint("b-accept", "op@conduction.nl")
    # twee tickets + twee merktekens; de cap telt alleen de tickets
    with pytest.raises(burnstore.BurnstoreError, match="full"):
        burnstore.mint("c-accept", "op@conduction.nl")
