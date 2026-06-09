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


def test_credentials_sets_source_url_and_interface_id():
    doc = _doc(sources=[{"slug": "demo-xxllnc"}])
    client = FakeClient([{"id": 1, "slug": "demo-xxllnc",
                          "configuration": {"headers.API-Interface-ID": "44"}}])
    provision.provision_credentials(client, doc, apikey="K",
                                    source_url="https://klant.example/api", interface_id="99")
    (_sid, body), = client.puts
    assert body["location"] == "https://klant.example/api"
    assert body["configuration"]["headers.API-Interface-ID"] == "99"   # overridden
    assert body["configuration"][HDR] == "K"


def test_credentials_keeps_config_defaults_when_url_and_iid_omitted():
    doc = _doc(sources=[{"slug": "demo-xxllnc"}])
    client = FakeClient([{"id": 1, "slug": "demo-xxllnc",
                          "configuration": {"headers.API-Interface-ID": "44"}}])
    provision.provision_credentials(client, doc, apikey="K")
    (_sid, body), = client.puts
    assert "location" not in body                       # not touched
    assert body["configuration"]["headers.API-Interface-ID"] == "44"   # preserved


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
    assert provision.resolve_password(_Args(password="p", password_env=None), "admin") == "p"
    monkeypatch.setenv("CRED", "from-env")
    assert provision.resolve_password(_Args(password=None, password_env="CRED"), "admin") == "from-env"


def test_resolve_password_raises_when_absent_or_empty(monkeypatch):
    # non-tty under pytest -> no prompt fallback
    with pytest.raises(provision.ProvisionError, match="provide --password"):
        provision.resolve_password(_Args(password=None, password_env=None), "admin")
    monkeypatch.delenv("EMPTY", raising=False)
    with pytest.raises(provision.ProvisionError, match="empty"):
        provision.resolve_password(_Args(password=None, password_env="EMPTY"), "admin")


def test_resolve_user_flag_or_error_without_tty():
    assert provision.resolve_user(_Args(user="bob")) == "bob"
    with pytest.raises(provision.ProvisionError, match="provide --user"):
        provision.resolve_user(_Args(user=None))  # non-tty under pytest


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


# --- oc-settings (OpenCatalogi register/schema coupling) ---


class FakeOcSettingsClient:
    """Serves registers/schemas slug->id and echoes the posted settings back."""

    def __init__(self, registers, schemas, reflect=True):
        self._registers = registers
        self._schemas = schemas
        self._reflect = reflect
        self._posted = {}

    def get(self, path):
        if path.endswith("/api/registers"):
            return {"results": self._registers}
        if path.endswith("/api/schemas"):
            return {"results": self._schemas}
        return {"configuration": self._posted}  # OC settings GET

    def post(self, path, body=None):
        self.last = body
        if self._reflect:
            self._posted = dict(body)
        return self._posted


def test_oc_settings_couples_each_type_to_resolved_ids():
    schemas = [{"slug": t, "id": i} for i, t in enumerate(provision.OC_OBJECT_TYPES, start=2)]
    client = FakeOcSettingsClient(registers=[{"slug": "publication", "id": 1}], schemas=schemas)
    provision.provision_oc_settings(client)
    # every object type got source=openregister, register=1, schema=<its id>
    for t in provision.OC_OBJECT_TYPES:
        assert client.last[f"{t}_source"] == "openregister"
        assert client.last[f"{t}_register"] == "1"
    assert client.last["catalog_schema"] == "2"  # first type -> id 2


def test_oc_settings_raises_when_schema_missing():
    client = FakeOcSettingsClient(
        registers=[{"slug": "publication", "id": 1}],
        schemas=[{"slug": "catalog", "id": 2}],  # the other types' schemas absent
    )
    with pytest.raises(provision.ProvisionError, match="schema"):
        provision.provision_oc_settings(client)


def test_oc_settings_raises_when_register_missing():
    schemas = [{"slug": t, "id": i} for i, t in enumerate(provision.OC_OBJECT_TYPES, start=2)]
    client = FakeOcSettingsClient(registers=[], schemas=schemas)
    with pytest.raises(provision.ProvisionError, match="register 'publication' not found"):
        provision.provision_oc_settings(client)


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


# --- import (idempotent: skip when already present) ---


class FakeImportClient:
    def __init__(self, present):
        self._present = present          # {bucket: [slugs]}
        self.uploaded = False

    def get(self, path):
        for bucket, p in provision.VERIFY_BUCKETS.items():
            if path == p:
                return {"results": [{"slug": s} for s in self._present.get(bucket, [])]}
        return {"results": []}

    def post_file(self, path, filename, content):
        self.uploaded = True
        return '{"message": "Import successful"}'


def test_import_skips_when_all_present():
    doc = _doc(schemas=[{"slug": "a"}, {"slug": "b"}], sources=[{"slug": "s"}])
    client = FakeImportClient({"schemas": ["a", "b"], "sources": ["s"]})
    provision.provision_import(client, doc)
    assert client.uploaded is False


def test_import_uploads_when_something_missing():
    doc = _doc(schemas=[{"slug": "a"}, {"slug": "b"}])
    client = FakeImportClient({"schemas": ["a"]})   # b missing
    provision.provision_import(client, doc)
    assert client.uploaded is True


def test_import_force_uploads_even_when_present():
    doc = _doc(schemas=[{"slug": "a"}])
    client = FakeImportClient({"schemas": ["a"]})
    provision.provision_import(client, doc, force=True)
    assert client.uploaded is True


# --- jobs (resolve synchronizationId slug -> numeric id) ---


class FakeJobsClient:
    """Serves syncs + jobs lists; PUT echoes the merged arguments back."""

    def __init__(self, syncs, jobs, reflect=True):
        self._syncs = syncs
        self._jobs = {j["id"]: dict(j) for j in jobs}
        self._reflect = reflect
        self.puts = []

    def get(self, path):
        if path.endswith("/synchronizations"):
            return {"results": self._syncs}
        if path.endswith("/jobs"):
            return {"results": list(self._jobs.values())}
        raise AssertionError(path)

    def put(self, path, body):
        jid = int(path.rsplit("/", 1)[1])
        self.puts.append((jid, body))
        if not self._reflect:
            return {"id": jid}  # nothing reflects -> provisioner sees a mismatch
        self._jobs[jid].update(body)
        return dict(self._jobs[jid])


def test_jobs_resolves_sync_slug_to_numeric():
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "sync-a"}}])
    client = FakeJobsClient(
        syncs=[{"slug": "sync-a", "id": 65}],
        jobs=[{"slug": "job-a", "id": 65, "arguments": {"synchronizationId": "sync-a"}}],
    )
    assert provision.provision_jobs(client, doc) == 1
    (_jid, body), = client.puts
    assert body["arguments"]["synchronizationId"] == 65


def test_jobs_skips_when_tenant_already_resolved():
    # tenant job already has the numeric id and no userId change requested -> no PUT
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "sync-a"}}])
    client = FakeJobsClient(syncs=[{"slug": "sync-a", "id": 65}],
                            jobs=[{"slug": "job-a", "id": 65, "arguments": {"synchronizationId": 65}}])
    assert provision.provision_jobs(client, doc) == 0
    assert client.puts == []


def test_jobs_sets_user_even_when_sync_id_already_resolved():
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "sync-a"}}])
    client = FakeJobsClient(syncs=[{"slug": "sync-a", "id": 65}],
                            jobs=[{"slug": "job-a", "id": 65, "arguments": {"synchronizationId": 65}}])
    assert provision.provision_jobs(client, doc, job_user="admin") == 1
    (_jid, body), = client.puts
    assert body == {"userId": "admin"}          # only userId; sync id already resolved


def test_jobs_sets_sync_id_and_user_together():
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "sync-a"}}])
    client = FakeJobsClient(syncs=[{"slug": "sync-a", "id": 65}],
                            jobs=[{"slug": "job-a", "id": 65,
                                   "arguments": {"synchronizationId": "sync-a"}}])
    provision.provision_jobs(client, doc, job_user="svc")
    (_jid, body), = client.puts
    assert body["arguments"]["synchronizationId"] == 65 and body["userId"] == "svc"


def test_jobs_raises_on_unresolvable_sync_slug():
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "ghost"}}])
    client = FakeJobsClient(syncs=[{"slug": "sync-a", "id": 65}],
                            jobs=[{"slug": "job-a", "id": 65}])
    with pytest.raises(provision.ProvisionError, match="not a tenant sync slug"):
        provision.provision_jobs(client, doc)


def test_jobs_raises_when_not_reflected():
    doc = _doc(jobs=[{"slug": "job-a", "arguments": {"synchronizationId": "sync-a"}}])
    client = FakeJobsClient(syncs=[{"slug": "sync-a", "id": 65}],
                            jobs=[{"slug": "job-a", "id": 65}], reflect=False)
    with pytest.raises(provision.ProvisionError, match="did not reflect"):
        provision.provision_jobs(client, doc)


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


# --- catalog ---


class FakeCatalogClient:
    """Serves registers/schemas slug->id lists and the catalog object; records PUT."""

    def __init__(self, registers, schemas, catalog, reflect=True):
        self._registers = registers
        self._schemas = schemas
        self._catalog = dict(catalog)
        self._reflect = reflect
        self.put_body = None

    def get(self, path):
        if path.endswith("/api/registers"):
            return {"results": self._registers}
        if path.endswith("/api/schemas"):
            return {"results": self._schemas}
        return dict(self._catalog)  # the catalog object path

    def put(self, path, body):
        self.put_body = body
        if self._reflect:
            self._catalog.update({"registers": body["registers"], "schemas": body["schemas"]})
        return self._catalog


def test_provision_catalog_resolves_slugs_to_ids():
    doc = _doc(schemas=[{"slug": "a"}, {"slug": "b"}, {"slug": "c"}])
    client = FakeCatalogClient(
        registers=[{"slug": "woo", "id": 2}, {"slug": "publication", "id": 1}],
        schemas=[{"slug": "a", "id": 9}, {"slug": "b", "id": 10}, {"slug": "c", "id": 11}],
        catalog={"slug": "publications", "title": "Publications", "registers": [2], "schemas": [99]},
    )
    provision.provision_catalog(client, doc)
    # all three config schema slugs resolved to ids; woo register resolved
    assert client.put_body["registers"] == [2]
    assert client.put_body["schemas"] == [9, 10, 11]
    assert client.put_body["title"] == "Publications"  # existing field preserved


def test_provision_catalog_raises_on_missing_schema():
    doc = _doc(schemas=[{"slug": "a"}, {"slug": "gone"}])
    client = FakeCatalogClient(
        registers=[{"slug": "woo", "id": 2}],
        schemas=[{"slug": "a", "id": 9}],  # 'gone' absent
        catalog={"slug": "publications", "registers": [], "schemas": []},
    )
    with pytest.raises(provision.ProvisionError, match="not on the tenant"):
        provision.provision_catalog(client, doc)


def test_provision_catalog_raises_when_register_missing():
    doc = _doc(schemas=[{"slug": "a"}])
    client = FakeCatalogClient(
        registers=[{"slug": "publication", "id": 1}],  # no 'woo'
        schemas=[{"slug": "a", "id": 9}],
        catalog={"slug": "publications"},
    )
    with pytest.raises(provision.ProvisionError, match="register 'woo' not found"):
        provision.provision_catalog(client, doc)


# --- all (orchestrator) ---


def _patch_steps(monkeypatch, calls, verify_missing=None, dangling=None):
    monkeypatch.setattr(provision, "provision_settings",
                        lambda c, o, m: calls.append("settings"))
    monkeypatch.setattr(provision, "provision_oc_settings",
                        lambda c: calls.append("oc-settings"))
    monkeypatch.setattr(provision, "provision_import",
                        lambda c, d, **kw: calls.append("import"))
    monkeypatch.setattr(provision, "verify_import",
                        lambda c, d: (calls.append("verify"),
                                      {"schemas": {"expected": 1, "missing": verify_missing or []}})[1])
    monkeypatch.setattr(provision, "provision_catalog",
                        lambda c, d: calls.append("catalog"))
    monkeypatch.setattr(provision, "provision_credentials",
                        lambda c, d, **kw: calls.append("credentials"))
    monkeypatch.setattr(provision, "sync_check",
                        lambda c, d: (calls.append("sync-check"),
                                      {"total": 1, "dangling": dangling or []})[1])
    monkeypatch.setattr(provision, "provision_jobs",
                        lambda c, d, **kw: calls.append("jobs") or 0)
    monkeypatch.setattr(provision, "provision_sync_run",
                        lambda c, d, mode="run": calls.append(f"sync-run:{mode}"))


def test_provision_all_runs_steps_in_order(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    provision.provision_all(None, _doc(), settings={"organisation": {}, "multitenancy": {}})
    assert calls == ["settings", "oc-settings", "import", "verify",
                     "catalog", "credentials", "sync-check", "jobs"]


def test_provision_all_skips_optional_steps(monkeypatch):
    calls = []
    _patch_steps(monkeypatch, calls)
    provision.provision_all(None, _doc(), settings=None, oc_settings=False, do_import=False,
                            catalog=False, run_syncs=True, sync_mode="test")
    assert calls == ["verify", "credentials", "sync-check", "jobs", "sync-run:test"]


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
