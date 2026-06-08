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


# --- settings (org + multitenancy) ---


class FakeSettingsClient:
    """Stores PUT payloads and serves them back wrapped under `key`, like the API."""

    def __init__(self, reflect=True, extra=None):
        self._store = {}
        self._reflect = reflect
        self._extra = extra or {}

    def put(self, path, body):
        self._store[path] = dict(body) if self._reflect else {}
        self.last = (path, body)
        return {}

    def get(self, path):
        key = "organisation" if path.endswith("organisation") else "multitenancy"
        value = dict(self._store.get(path, {}))
        value.update(self._extra)  # server may add fields we didn't send
        return {key: value}


def test_provision_settings_asserts_reflection_ignoring_extra_fields():
    # server echoes back an extra auto-created org id; we only assert what we sent
    client = FakeSettingsClient(extra={"default_organisation": "auto-made-uuid"})
    out = provision.provision_settings(
        client,
        {"auto_create_default_organisation": True},
        {"enabled": False, "adminOverride": True},
    )
    assert out["organisation"]["auto_create_default_organisation"] is True
    assert out["multitenancy"]["enabled"] is False


def test_provision_settings_raises_when_not_reflected():
    client = FakeSettingsClient(reflect=False)
    with pytest.raises(provision.ProvisionError, match="did not reflect"):
        provision.provision_settings(client, {"auto_create_default_organisation": True}, {"enabled": False})


# --- sync-run ---


class FakeRunClient:
    """Lists syncs and records run/test POSTs; optional per-id error response."""

    def __init__(self, syncs, errors=None):
        self._syncs = syncs
        self._errors = errors or {}
        self.posts = []

    def get(self, path):
        return {"results": self._syncs}

    def post(self, path, body=None):
        self.posts.append(path)
        sid = int(path.split("/")[-2])
        return self._errors.get(sid, {"ok": True})


def test_sync_run_posts_run_for_each_config_sync():
    doc = _doc(synchronizations=[{"slug": "x"}, {"slug": "y"}])
    client = FakeRunClient([{"slug": "x", "id": 1}, {"slug": "y", "id": 2}])
    done = provision.provision_sync_run(client, doc, mode="run")
    assert [d["id"] for d in done] == [1, 2]
    assert client.posts == [f"{provision.SYNCS_PATH}/1/run", f"{provision.SYNCS_PATH}/2/run"]


def test_sync_run_test_mode_uses_test_endpoint():
    doc = _doc(synchronizations=[{"slug": "x"}])
    client = FakeRunClient([{"slug": "x", "id": 1}])
    provision.provision_sync_run(client, doc, mode="test")
    assert client.posts == [f"{provision.SYNCS_PATH}/1/test"]


def test_sync_run_raises_on_error_response():
    doc = _doc(synchronizations=[{"slug": "x"}])
    client = FakeRunClient([{"slug": "x", "id": 1}], errors={1: {"error": "boom"}})
    with pytest.raises(provision.ProvisionError, match="boom"):
        provision.provision_sync_run(client, doc, mode="run")


def test_sync_run_raises_when_sync_missing():
    doc = _doc(synchronizations=[{"slug": "ghost"}])
    client = FakeRunClient([{"slug": "x", "id": 1}])
    with pytest.raises(provision.ProvisionError, match="not found"):
        provision.provision_sync_run(client, doc, mode="run")


# --- objects ---


class FakeObjectClient:
    def __init__(self, response):
        self._response = response
        self.posts = []

    def post(self, path, body=None):
        self.posts.append((path, body))
        return self._response


def test_provision_object_returns_created_with_id():
    client = FakeObjectClient({"id": 42, "title": "Home"})
    obj = provision.provision_object(client, "woo", "page", {"title": "Home"})
    assert obj["id"] == 42
    assert client.posts == [(f"{provision.OBJECTS_PATH}/woo/page", {"title": "Home"})]


def test_provision_object_raises_without_id():
    client = FakeObjectClient({"message": "no id here"})
    with pytest.raises(provision.ProvisionError, match="no id/uuid"):
        provision.provision_object(client, "woo", "page", {"title": "Home"})


# --- all (orchestrator) ---


def _patch_steps(monkeypatch, calls, verify_missing=None, dangling=None):
    monkeypatch.setattr(provision, "provision_settings",
                        lambda c, o, m: calls.append("settings"))
    monkeypatch.setattr(provision, "verify_import",
                        lambda c, d: (calls.append("verify"),
                                      {"schemas": {"expected": 1, "missing": verify_missing or []}})[1])
    monkeypatch.setattr(provision, "provision_credentials",
                        lambda c, d, apikey=None: calls.append("credentials"))
    monkeypatch.setattr(provision, "sync_check",
                        lambda c, d: (calls.append("sync-check"),
                                      {"total": 1, "dangling": dangling or []})[1])
    monkeypatch.setattr(provision, "provision_sync_run",
                        lambda c, d, mode="run": calls.append(f"sync-run:{mode}"))


def test_provision_all_runs_steps_in_order(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    provision.provision_all(None, _doc(), settings={"organisation": {}, "multitenancy": {}})
    assert calls == ["settings", "verify", "credentials", "sync-check"]  # sync-run skipped


def test_provision_all_includes_sync_run_when_requested(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    provision.provision_all(None, _doc(), settings=None, run_syncs=True, sync_mode="test")
    assert calls == ["verify", "credentials", "sync-check", "sync-run:test"]  # settings skipped


def test_provision_all_stops_on_incomplete_import(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls, verify_missing=["convenanten"])
    with pytest.raises(provision.ProvisionError, match="import incomplete"):
        provision.provision_all(None, _doc(), settings=None)
    assert "credentials" not in calls  # stopped before credentials


def test_provision_all_stops_on_dangling_syncs(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls, dangling=[{"slug": "x", "targetId": "2/x"}])
    with pytest.raises(provision.ProvisionError, match="dangling"):
        provision.provision_all(None, _doc(), settings=None)
