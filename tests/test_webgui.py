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
    assert b"OpenWoo-platform" in resp.data
    # links to both use cases + logout
    assert b'href="/tenant"' in resp.data
    assert b'href="/provision-config"' in resp.data
    assert b'href="/logout"' in resp.data


def test_provision_config_form_renders(client):
    resp = client.get("/provision-config")
    assert resp.status_code == 200
    assert b"Omgeving inrichten" in resp.data
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


def test_provision_in_cluster_targets_internal_service(client, monkeypatch):
    """The in_cluster checkbox rewrites --base to the tenant's cluster-local
    Service and adds --host-header with the public host."""
    captured = {}

    class FakePopen:
        def __init__(self, argv, env=None, cwd=None, **kw):
            captured["argv"] = argv
            self.stdout = iter(["ok\n"])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(server.subprocess, "Popen", FakePopen)

    resp = client.post("/provision", data={
        "base": "https://noorderzijlvest.commonground.nu",
        "user": "admin", "in_cluster": "on",
    })
    assert resp.status_code == 200
    # De route streamt: Popen draait pas in de generator, dus de body moet
    # geconsumeerd zijn vóór er iets in `captured` staat.
    assert "exit code 0" in resp.get_data(as_text=True)
    argv = captured["argv"]
    assert argv[argv.index("--base") + 1] == \
        "http://nextcloud.noorderzijlvest-prod.svc.cluster.local:8080"
    assert argv[argv.index("--host-header") + 1] == "noorderzijlvest.commonground.nu"


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
    assert b"OpenWoo-platform" in resp.data


def test_healthz_open_even_with_auth(authed_client):
    # The k8s probe must work without an identity header.
    assert authed_client.get("/healthz").status_code == 200


# --- Phase 3: tenant creation via PR (/tenant) ---

def test_tenant_form_renders(client):
    resp = client.get("/tenant")
    assert resp.status_code == 200
    assert b"Nieuwe WOO-omgeving" in resp.data
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


def test_tenant_branding_extras_reach_the_pr(client, monkeypatch):
    """The optional branding fields end up in the proposed tenant file, so a
    themed tenant needs no hand-edit after the PR is merged."""
    captured = {}

    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: captured.update(kw) or {"number": 8, "html_url": "u/8"})
    resp = client.post("/tenant", data={
        "org": "almere", "environment": "accept",
        "frontend_theme": "almere-theme",
        "frontend_jumbotron": "https://ex.org/j.jpg",
        "frontend_favicon": "https://ex.org/f.ico",
    })
    assert resp.status_code == 201
    c = captured["content"]
    assert 'themeClassname: "almere-theme"' in c
    assert 'jumbotronImageUrl: "https://ex.org/j.jpg"' in c
    assert 'faviconUrl: "https://ex.org/f.ico"' in c


def test_tenant_without_theme_leaves_the_baseline_alone(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: captured.update(kw) or {"number": 9, "html_url": "u/9"})
    client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert "themeClassname" not in captured["content"]


def test_tenant_form_offers_the_branding_fields(client):
    body = client.get("/tenant").get_data(as_text=True)
    for field in ("frontend_theme", "frontend_jumbotron", "frontend_favicon"):
        assert f'name="{field}"' in body


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


def test_batch_form_renders(client):
    resp = client.get("/tenant/batch")
    assert resp.status_code == 200
    assert "Meerdere omgevingen aanmaken".encode() in resp.data and b'name="orgs"' in resp.data


def test_batch_happy_one_pr_many_files(client, monkeypatch):
    captured = {}

    def fake(**kw):
        captured.update(kw)
        return {"number": 11, "html_url": "https://x/pulls/11"}

    monkeypatch.setattr(server.gitlib, "propose_files", fake)
    resp = client.post("/tenant/batch", data={"orgs": "almere\nbaarn\nsoest", "environment": "accept"})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["count"] == 3 and body["pr_number"] == 11
    assert {p for p, _ in captured["files"]} == {
        "nextcloud-platform/values/tenants/tenant-almere-accept.yaml",
        "nextcloud-platform/values/tenants/tenant-baarn-accept.yaml",
        "nextcloud-platform/values/tenants/tenant-soest-accept.yaml",
    }


def test_batch_rejects_bad_org_and_dupes(client, monkeypatch):
    monkeypatch.setattr(server.gitlib, "propose_files", lambda **kw: 1 / 0)  # must not be called
    bad = client.post("/tenant/batch", data={"orgs": "almere-accept\nbaarn", "environment": "accept"})
    assert bad.status_code == 400
    dup = client.post("/tenant/batch", data={"orgs": "almere\nalmere", "environment": "accept"})
    assert dup.status_code == 400


def test_delete_form_prefills_tenant(client):
    resp = client.get("/tenant/delete?tenant=almere-accept")
    assert resp.status_code == 200
    assert b'value="almere-accept"' in resp.data and "volumes".encode() in resp.data


def test_delete_happy_opens_pr(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.gitlib, "propose_deletion",
                        lambda **kw: captured.update(kw) or {"number": 12, "html_url": "u"})
    resp = client.post("/tenant/delete", data={"tenant": "almere-accept"})
    assert resp.status_code == 201
    assert resp.get_json()["tenant"] == "almere-accept"
    assert captured["path"] == "nextcloud-platform/values/tenants/tenant-almere-accept.yaml"


def test_delete_missing_file_is_404(client, monkeypatch):
    def boom(**kw):
        raise server.gitlib.GitlibError(404, "file not found")
    monkeypatch.setattr(server.gitlib, "propose_deletion", boom)
    resp = client.post("/tenant/delete", data={"tenant": "ghost-accept"})
    assert resp.status_code == 404


def test_delete_rejects_bad_name(client):
    assert client.post("/tenant/delete", data={"tenant": "Bad!"}).status_code == 400


def test_pr_status_proxies_gitlib(client, monkeypatch):
    monkeypatch.setattr(server.gitlib, "get_pr",
                        lambda n: {"state": "open", "merged": False, "html_url": "u"})
    resp = client.get("/tenant/pr-status?number=7")
    assert resp.status_code == 200
    assert resp.get_json()["merged"] is False


def test_pr_status_rejects_non_numeric(client):
    resp = client.get("/tenant/pr-status?number=abc")
    assert resp.status_code == 400


def test_argo_status_proxies_argolib(client, monkeypatch):
    monkeypatch.setattr(server.argolib, "app_status",
                        lambda name: {"exists": True, "sync": "Synced", "health": "Healthy"})
    resp = client.get("/tenant/argo-status?tenant=almere-accept")
    assert resp.status_code == 200
    assert resp.get_json()["health"] == "Healthy"


def test_argo_status_rejects_bad_tenant(client):
    resp = client.get("/tenant/argo-status?tenant=Bad_Name!")
    assert resp.status_code == 400


def test_logout_redirects_via_signout_to_keycloak(client):
    resp = client.get("/logout")
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert loc.startswith("/oauth2/sign_out?")
    # the rd target is Keycloak's end-session endpoint (url-encoded inside rd)
    assert "iam.commonground.nu" in loc and "openid-connect%2Flogout" in loc


def test_dashboard_combines_sources(client, monkeypatch):
    monkeypatch.setattr(server.argolib, "list_apps",
                        lambda: [{"name": "nc-almere-accept", "tenant": "almere-accept",
                                  "sync": "Synced", "health": "Healthy"}])
    monkeypatch.setattr(server.gitlib, "list_prs",
                        lambda: [{"number": 5, "tenant": "almere-accept", "state": "open",
                                  "merged": False, "html_url": "u", "title": "add"}])
    d = client.get("/dashboard.json").get_json()
    assert d["tenants"][0]["tenant"] == "almere-accept"
    assert d["prs"][0]["number"] == 5
    assert d["errors"] == []


def test_dashboard_is_resilient_to_partial_failure(client, monkeypatch):
    def boom():
        raise server.argolib.ArgoError(0, "kube unreachable")
    monkeypatch.setattr(server.argolib, "list_apps", boom)
    monkeypatch.setattr(server.gitlib, "list_prs", lambda: [])
    resp = client.get("/dashboard.json")
    assert resp.status_code == 200  # page still loads
    assert any("argo" in e for e in resp.get_json()["errors"])


# --- secret-reveal flow (change tenant-onboarding-completion, sectie 4) ---


@pytest.fixture
def reveal_on(monkeypatch):
    """Feature flag on, with an in-memory ticket store and a known password."""
    monkeypatch.setattr(server, "REVEAL_ENABLED", True)
    tickets = {}
    monkeypatch.setattr(server.burnstore, "_load", lambda: (dict(tickets), "1"))
    monkeypatch.setattr(server.burnstore, "_save",
                        lambda t, v: (tickets.clear(), tickets.update(t)))
    monkeypatch.setattr(server.burnstore, "read_admin_password",
                        lambda tenant: "heiligboontje" if tenant == "almere-accept" else None)
    return tickets


def _mint(client, tenant="almere-accept"):
    resp = client.post(f"/tenant/{tenant}/secret-link")
    return resp, resp.get_json() or {}


def test_secret_link_mints_a_url(client, reveal_on):
    resp, body = _mint(client)
    assert resp.status_code == 201
    assert "/reveal/" in body["reveal_url"] and body["tenant"] == "almere-accept"
    # the password must NOT be in the mint response
    assert "heiligboontje" not in resp.get_data(as_text=True)


def test_reveal_shows_the_password_once(client, reveal_on):
    _resp, body = _mint(client)
    path = "/reveal/" + body["reveal_url"].split("/reveal/")[1]
    first = client.get(path)
    assert first.status_code == 200
    assert "heiligboontje" in first.get_data(as_text=True)
    # second read is gone
    assert client.get(path).status_code == 404


def test_reveal_of_an_unknown_token_is_404(client, reveal_on):
    assert client.get("/reveal/ditbestaatniet").status_code == 404


def test_reveal_needs_no_operator_identity(monkeypatch, reveal_on):
    """The PO has no Keycloak account: /reveal must pass the auth gate, while
    minting must not."""
    monkeypatch.setattr(server, "REQUIRE_AUTH", True)
    server.app.config["TESTING"] = True
    c = server.app.test_client()
    # minting without an operator identity is refused
    assert c.post("/tenant/almere-accept/secret-link").status_code == 403
    # revealing is not (404 = no such ticket, i.e. it got past the gate)
    assert c.get("/reveal/whatever").status_code == 404


def test_secret_link_refuses_a_tenant_without_a_password(client, reveal_on):
    resp, _body = _mint(client, "bestaatniet-accept")
    assert resp.status_code == 404
    assert "nextcloud-secrets" in resp.get_data(as_text=True)


def test_secret_link_rejects_an_invalid_tenant_name(client, reveal_on):
    assert client.post("/tenant/Not_Valid/secret-link").status_code in (400, 404)


def test_reveal_is_off_by_default(client, monkeypatch):
    monkeypatch.setattr(server, "REVEAL_ENABLED", False)
    assert client.post("/tenant/almere-accept/secret-link").status_code == 404
    assert client.get("/reveal/anything").status_code == 404


def test_password_never_reaches_the_logs(client, reveal_on, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        _resp, body = _mint(client)
        client.get("/reveal/" + body["reveal_url"].split("/reveal/")[1])
    assert "heiligboontje" not in caplog.text
    # but the fact of it is audited
    assert "secret-link minted" in caplog.text and "reveal claimed" in caplog.text


def test_reveal_page_warns_it_is_single_use(client, reveal_on):
    _resp, body = _mint(client)
    page = client.get("/reveal/" + body["reveal_url"].split("/reveal/")[1])
    text = page.get_data(as_text=True)
    assert "eenmalig" in text.lower()
    assert "noindex" in text            # never indexed by a crawler
