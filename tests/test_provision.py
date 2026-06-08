# SPDX-License-Identifier: EUPL-1.2
"""Unit tests for the post-import provisioner (pure logic, no live stack)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import provision  # noqa: E402


def _doc(**comps):
    return {"openapi": "3.0.0", "info": {"title": "t"}, "components": comps}


# --- pure helpers ---


def test_config_source_slugs_from_list_and_dict():
    as_list = _doc(sources=[{"slug": "a"}, {"slug": "b"}])
    as_dict = _doc(sources={"a": {"slug": "a"}, "b": {"slug": "b"}})
    assert provision.config_source_slugs(as_list) == ["a", "b"]
    assert sorted(provision.config_source_slugs(as_dict)) == ["a", "b"]


def test_config_source_slugs_skips_entries_without_slug():
    doc = _doc(sources=[{"slug": "a"}, {"name": "no-slug"}])
    assert provision.config_source_slugs(doc) == ["a"]


def test_results_list_unwraps_wrapper_and_bare():
    assert provision.results_list({"results": [1, 2]}) == [1, 2]
    assert provision.results_list([1, 2]) == [1, 2]
    assert provision.results_list({"results": "nope"}) == []
    assert provision.results_list("nope") == []


def test_find_by_slug_prefers_slug_then_name():
    items = [{"slug": "x", "id": 1}, {"name": "y", "id": 2}]
    assert provision.find_by_slug(items, "x")["id"] == 1
    assert provision.find_by_slug(items, "y")["id"] == 2  # name fallback
    assert provision.find_by_slug(items, "z") is None


def test_dummy_apikey_is_deterministic_and_marked():
    key = provision.dummy_apikey("demo-xxllnc")
    assert key == provision.dummy_apikey("demo-xxllnc")
    assert key.startswith(provision.DUMMY_APIKEY_PREFIX)
    assert "demo-xxllnc" in key


# --- credentials flow against an in-memory fake client ---


HDR = provision.API_KEY_HEADER


def test_merge_header_preserves_existing_and_handles_empty():
    existing = {"headers.API-Interface-ID": "44"}
    merged = provision.merge_header(existing, HDR, "k")
    assert merged == {"headers.API-Interface-ID": "44", HDR: "k"}
    # OpenConnector serializes an empty configuration as a list -> treat as empty
    assert provision.merge_header([], HDR, "k") == {HDR: "k"}


class FakeClient:
    """Records PUTs and serves a per-id source so the GET assertion can pass."""

    def __init__(self, sources, reflect=True):
        self._sources = {s["id"]: dict(s) for s in sources}
        self._reflect = reflect
        self.puts = []

    def get(self, path):
        if path.endswith("/api/sources"):
            return {"results": list(self._sources.values())}
        source_id = int(path.rsplit("/", 1)[1])
        return self._sources[source_id]

    def put(self, path, body):
        source_id = int(path.rsplit("/", 1)[1])
        self.puts.append((source_id, body))
        if self._reflect:
            self._sources[source_id].update(body)
        return self._sources[source_id]


def test_credentials_writes_dummy_key_into_header_preserving_config():
    doc = _doc(sources=[{"slug": "demo-xxllnc"}])
    client = FakeClient(
        [{"id": 1, "slug": "demo-xxllnc",
          "configuration": {"headers.API-Interface-ID": "44"}}]
    )
    count = provision.provision_credentials(client, doc)
    assert count == 1
    (sid, body), = client.puts
    assert sid == 1
    # existing header preserved, dummy key added under the API-KEY header
    assert body["configuration"]["headers.API-Interface-ID"] == "44"
    assert body["configuration"][HDR] == provision.dummy_apikey("demo-xxllnc")


def test_credentials_uses_supplied_real_key():
    doc = _doc(sources=[{"slug": "demo-xxllnc"}])
    client = FakeClient([{"id": 1, "slug": "demo-xxllnc", "configuration": {}}])
    provision.provision_credentials(client, doc, apikey="REAL-KEY")
    (_sid, body), = client.puts
    assert body["configuration"][HDR] == "REAL-KEY"


def test_credentials_raises_when_source_missing_on_instance():
    doc = _doc(sources=[{"slug": "ghost"}])
    client = FakeClient([{"id": 1, "slug": "demo-xxllnc"}])
    with pytest.raises(provision.ProvisionError, match="not found"):
        provision.provision_credentials(client, doc)


def test_credentials_raises_when_key_does_not_reflect():
    doc = _doc(sources=[{"slug": "demo-xxllnc"}])
    client = FakeClient(
        [{"id": 1, "slug": "demo-xxllnc", "configuration": {}}], reflect=False
    )
    with pytest.raises(provision.ProvisionError, match="did not reflect"):
        provision.provision_credentials(client, doc)


def test_credentials_noop_when_no_sources():
    assert provision.provision_credentials(FakeClient([]), _doc()) == 0


# --- credential resolution (kept out of argv) ---


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_resolve_password_prefers_flag_then_env(monkeypatch):
    assert provision.resolve_password(_Args(password="p", password_env=None)) == "p"
    monkeypatch.setenv("CRED", "from-env")
    assert provision.resolve_password(_Args(password=None, password_env="CRED")) == "from-env"


def test_resolve_password_raises_when_absent_or_empty(monkeypatch):
    with pytest.raises(provision.ProvisionError, match="provide --password"):
        provision.resolve_password(_Args(password=None, password_env=None))
    monkeypatch.delenv("EMPTY", raising=False)
    with pytest.raises(provision.ProvisionError, match="empty"):
        provision.resolve_password(_Args(password=None, password_env="EMPTY"))


def test_resolve_apikey_dummy_when_unset():
    assert provision.resolve_apikey(_Args(apikey=None, apikey_env=None)) is None


def test_resolve_apikey_from_env(monkeypatch):
    monkeypatch.setenv("KEY", "real")
    assert provision.resolve_apikey(_Args(apikey=None, apikey_env="KEY")) == "real"


# --- verify-import (slug diff) ---


class FakeListClient:
    """Serves canned list responses keyed by endpoint path."""

    def __init__(self, by_path):
        self._by_path = by_path

    def get(self, path):
        return {"results": self._by_path.get(path, [])}


def test_verify_import_reports_missing_slugs():
    doc = _doc(
        schemas=[{"slug": "a"}, {"slug": "b"}, {"slug": "c"}],
        sources=[{"slug": "s1"}],
    )
    client = FakeListClient({
        provision.SCHEMAS_PATH: [{"slug": "a"}, {"slug": "c"}],   # b dropped
        provision.SOURCES_PATH: [{"slug": "s1"}],
    })
    report = provision.verify_import(client, doc)
    assert report["schemas"] == {"expected": 3, "missing": ["b"]}
    assert report["sources"] == {"expected": 1, "missing": []}
    assert "synchronizations" not in report  # no syncs in config -> skipped


def test_verify_import_all_present():
    doc = _doc(schemas=[{"slug": "a"}, {"slug": "b"}])
    client = FakeListClient({provision.SCHEMAS_PATH: [{"slug": "b"}, {"slug": "a"}]})
    assert provision.verify_import(client, doc)["schemas"]["missing"] == []


# --- sync-check (dangling target schema) ---


def test_target_schema_resolved():
    assert provision.target_schema_resolved("2/19") is True
    assert provision.target_schema_resolved("2/convenanten") is False
    assert provision.target_schema_resolved("woo/adviezen") is False
    assert provision.target_schema_resolved(None) is False
    assert provision.target_schema_resolved("2") is False


def test_sync_check_flags_dangling_targets():
    doc = _doc(synchronizations=[{"slug": "x"}, {"slug": "y"}])
    client = FakeListClient({provision.SYNCS_PATH: [
        {"slug": "x", "targetId": "2/19"},          # resolved
        {"slug": "y", "targetId": "2/convenanten"},  # dangling
    ]})
    result = provision.sync_check(client, doc)
    assert result["total"] == 2
    assert result["dangling"] == [{"slug": "y", "targetId": "2/convenanten"}]


def test_sync_check_ignores_syncs_not_in_config():
    doc = _doc(synchronizations=[{"slug": "x"}])
    client = FakeListClient({provision.SYNCS_PATH: [
        {"slug": "x", "targetId": "2/19"},
        {"slug": "other", "targetId": "2/dangling"},  # not ours -> ignored
    ]})
    assert provision.sync_check(client, doc)["dangling"] == []
