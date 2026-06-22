# SPDX-License-Identifier: EUPL-1.2
# Phase-1 tests for the hosted Flask control-plane (webgui/server.py).
#
# Skips entirely when Flask is not importable (the system-python `make test`
# run has no Flask); the webgui venv has it, so run there to exercise these:
#   webgui/.venv/bin/python -m pytest tests/test_webgui.py
"""Route + audit-logging tests for webgui/server.py (no real subprocess)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import server  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    # Route-behaviour tests run with auth disabled; the auth guard has its own
    # tests below. REQUIRE_AUTH is read per-request from the module global.
    monkeypatch.setattr(server, "REQUIRE_AUTH", False)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.data == b"ok\n"


def test_index_is_landing_with_usecase_links(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"OpenWoo platform" in resp.data
    # links to both use cases + logout
    assert b'href="/tenant"' in resp.data
    assert b'href="/provision-config"' in resp.data
    assert b'href="/oauth2/sign_out"' in resp.data


def test_provision_config_form_renders(client):
    resp = client.get("/provision-config")
    assert resp.status_code == 200
    assert b"OpenWoo tenant provisioning" in resp.data
    assert b'name="base"' in resp.data


def test_provision_missing_base_is_400(client):
    # build_command() raises ValueError without a base URL -> 400, no subprocess.
    resp = client.post("/provision", data={"user": "admin"})
    assert resp.status_code == 400
    assert b"error:" in resp.data


def test_provision_streams_subprocess_output(client, monkeypatch):
    """POST /provision spawns build_command()'s argv and streams stdout back.
    The real provision.py is replaced by a fake Popen so the test stays offline."""
    captured = {}

    class FakePopen:
        def __init__(self, argv, env=None, cwd=None, **kw):
            captured["argv"] = argv
            captured["env"] = env
            self.stdout = iter(["step one\n", "step two\n"])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(server.subprocess, "Popen", FakePopen)

    resp = client.post("/provision", data={
        "base": "https://canary.accept.commonground.nu",
        "user": "admin",
        "password": "s3cret",
    })
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "step one" in body and "step two" in body
    assert "exit code 0" in body
    # secret must travel via env, never argv
    assert "s3cret" not in " ".join(captured["argv"])
    assert "s3cret" in (captured["env"] or {}).get("GUI_PROVISION_PASSWORD", "")


def test_current_user_reads_proxy_header(client):
    with server.app.test_request_context(headers={"X-Forwarded-Email": "op@example.org"}):
        assert server.current_user() == "op@example.org"
    with server.app.test_request_context():
        assert server.current_user() == "-"


# --- Phase 2: fail-closed auth guard (REQUIRE_AUTH) ---

@pytest.fixture
def authed_client(monkeypatch):
    """Client with REQUIRE_AUTH ON (the production default)."""
    monkeypatch.setattr(server, "REQUIRE_AUTH", True)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_auth_required_blocks_unauthenticated(authed_client):
    # No identity header -> 403 on a real route...
    assert authed_client.get("/").status_code == 403
    assert authed_client.post("/provision", data={"base": "https://x"}).status_code == 403


def test_auth_required_allows_with_proxy_header(authed_client):
    resp = authed_client.get("/", headers={"X-Forwarded-Email": "op@conduction.nl"})
    assert resp.status_code == 200
    assert b"OpenWoo platform" in resp.data


def test_healthz_open_even_with_auth(authed_client):
    # The k8s probe must work without an identity header.
    assert authed_client.get("/healthz").status_code == 200


# --- Phase 3: tenant creation via PR (/tenant) ---

def test_tenant_form_renders(client):
    resp = client.get("/tenant")
    assert resp.status_code == 200
    assert b"Create a WOO tenant" in resp.data
    assert b'name="org"' in resp.data and b'name="environment"' in resp.data


def test_tenant_validation_error_is_400_no_pr(client, monkeypatch):
    # A full <org>-<env> in the org field must fail BEFORE any git call.
    called = {"n": 0}
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: called.__setitem__("n", called["n"] + 1))
    resp = client.post("/tenant", data={"org": "almere-accept", "environment": "accept"})
    assert resp.status_code == 400
    assert resp.get_json()["errors"]
    assert called["n"] == 0  # no PR attempted


def test_tenant_happy_derives_everything(client, monkeypatch):
    captured = {}

    def fake_propose(**kw):
        captured.update(kw)
        return {"number": 7, "html_url": "https://codeberg.org/x/pulls/7"}

    monkeypatch.setattr(server.gitlib, "propose_file", fake_propose)
    # operator types ONLY org + environment
    resp = client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["pr_url"].endswith("/pulls/7") and body["pr_number"] == 7
    assert body["tenant"] == "almere-accept"
    # derived: name, path, branch, all 3 apps, branding, ESO-managed
    assert captured["path"] == "nextcloud-platform/values/tenants/tenant-almere-accept.yaml"
    assert captured["branch"] == "add-tenant/almere-accept"
    c = captured["content"]
    assert "name: almere-accept" in c and "dbType: postgres" in c
    assert "- opencatalogi" in c and "- openconnector" in c and "- openregister" in c
    assert 'organisationName: "Gemeente Almere"' in c
    assert "managed: true" in c


def test_tenant_requester_stamped_from_proxy(authed_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: captured.update(kw) or {"number": 1, "html_url": "u"})
    resp = authed_client.post("/tenant", headers={"X-Forwarded-Email": "op@conduction.nl"},
                              data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 201
    assert "requested-by: op@conduction.nl" in captured["commit_message"]
    assert "op@conduction.nl" in captured["pr_body"]


def test_tenant_conflict_maps_to_409(client, monkeypatch):
    def boom(**kw):
        raise server.gitlib.GitlibError(409, "branch already exists")
    monkeypatch.setattr(server.gitlib, "propose_file", boom)
    resp = client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 409
    assert "already exists" in resp.get_json()["errors"][0]


def test_pr_status_proxies_gitlib(client, monkeypatch):
    monkeypatch.setattr(server.gitlib, "get_pr",
                        lambda n: {"state": "open", "merged": False, "html_url": "u"})
    resp = client.get("/tenant/pr-status?number=7")
    assert resp.status_code == 200
    assert resp.get_json()["merged"] is False


def test_pr_status_rejects_non_numeric(client):
    resp = client.get("/tenant/pr-status?number=abc")
    assert resp.status_code == 400
