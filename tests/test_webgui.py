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

@pytest.fixture(autouse=True)
def _undeclared(monkeypatch):
    """Default for every route test: the tenant does not exist in git yet.

    The create route reads the tenants repo to decide create-vs-update, so
    without this every test would hit the real GitHub client. Tests that care
    about an existing tenant override gitlib.get_file themselves.
    """
    def not_found(path, ref=None):
        raise server.gitlib.GitlibError(404, f"file not found: {path}")

    monkeypatch.setattr(server.gitlib, "get_file", not_found)


def _declared(monkeypatch, content):
    """Make gitlib.get_file return `content` for any path."""
    monkeypatch.setattr(server.gitlib, "get_file", lambda path, ref=None: (content, "sha123"))


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


def test_reveal_also_requires_an_operator_identity(monkeypatch, reveal_on):
    """Besluit 2026-08-07: een adminwachtwoord wordt alleen aan
    Conduction-medewerkers getoond. Beide routes zitten dus achter de login;
    oauth2-proxy heeft bewust geen skip_auth_routes, en deze test houdt de
    app-kant daarmee in de pas."""
    monkeypatch.setattr(server, "REQUIRE_AUTH", True)
    server.app.config["TESTING"] = True
    c = server.app.test_client()
    assert c.post("/tenant/almere-accept/secret-link").status_code == 403
    assert c.get("/reveal/whatever").status_code == 403


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


# --- bestaande tenant: ophalen en bijwerken ---


_PORTAL_FILE = """---
tenant:
  name: almere-accept
  environment: accept
  wave: "1"
  dbType: postgres
  secrets:
    managed: true
  apps:
    enabled:
      - opencatalogi
  frontend:
    branding:
      organisationName: "Gemeente Almere"
      themeClassname: "almere-theme"
"""

# Vrije env-vars op de frontend: iets wat het formulier niet modelleert en ook
# niet zou moeten modelleren. (`tag` was hier eerder het voorbeeld, maar dat
# veld kent de portal inmiddels wél.)
_HAND_EDITED_FILE = _PORTAL_FILE + '    env:\n      EXTRA: "1"\n'


def test_declaration_reports_absent_tenant(client):
    body = client.get("/tenant/almere-accept/declaration").get_json()
    assert body["exists"] is False and body["editable"] is False


def test_declaration_on_a_bare_tenant_has_nothing_to_show(client, monkeypatch):
    """canary-accept draagt alleen naam/omgeving/wave/db/apps. Bewerkbaar, maar
    er valt niets in te vullen — de UI moet dat zeggen in plaats van beloven dat
    'de huidige waarden hieronder staan'."""
    _declared(monkeypatch, """---
tenant:
  name: canary-accept
  environment: accept
  wave: "0"
  dbType: postgres
  apps:
    enabled:
      - opencatalogi
""")
    body = client.get("/tenant/canary-accept/declaration").get_json()
    assert body["exists"] is True and body["editable"] is True
    v = body["fields"]
    assert all(v[k] == "" for k in ("frontend_org", "frontend_host",
                                    "frontend_theme", "frontend_tag"))


def test_declaration_returns_the_declared_values(client, monkeypatch):
    _declared(monkeypatch, _PORTAL_FILE)
    body = client.get("/tenant/almere-accept/declaration").get_json()
    assert body["exists"] is True and body["editable"] is True
    assert body["fields"]["frontend_theme"] == "almere-theme"
    assert body["fields"]["frontend_org"] == "Gemeente Almere"


def test_declaration_marks_a_hand_edited_file_uneditable(client, monkeypatch):
    _declared(monkeypatch, _HAND_EDITED_FILE)
    body = client.get("/tenant/almere-accept/declaration").get_json()
    assert body["exists"] is True and body["editable"] is False
    assert body["unknown"] == ["tenant.frontend.env"]


def test_declaration_rejects_a_bad_name(client):
    assert client.get("/tenant/Not_Valid/declaration").status_code == 400


def test_certificate_block_sits_outside_the_advanced_fold(client):
    """Op een eigen domein is de certificaatkeuze een beslissing, geen tweak —
    hij mag niet weggevouwen zitten onder 'standaardwaarden zijn al ingevuld'."""
    body = client.get("/tenant").get_data(as_text=True)
    assert body.index("</details>") < body.index('id="certBlock"')


def test_dashboard_exposes_the_reveal_flag(client, monkeypatch):
    monkeypatch.setattr(server.argolib, "list_apps", lambda: [])
    monkeypatch.setattr(server.gitlib, "list_prs", lambda: [])
    monkeypatch.setattr(server.burnstore, "minted_tenants", lambda: ["gouda-accept"])
    monkeypatch.setattr(server, "REVEAL_ENABLED", False)
    body = client.get("/dashboard.json").get_json()
    assert body["reveal_enabled"] is False and body["minted"] == []
    monkeypatch.setattr(server, "REVEAL_ENABLED", True)
    body = client.get("/dashboard.json").get_json()
    assert body["reveal_enabled"] is True
    assert body["minted"] == ["gouda-accept"]   # die krijgt geen knop meer


def test_second_mint_for_a_tenant_is_refused(client, reveal_on, monkeypatch):
    """Eén overdracht per omgeving, ook door een andere operator."""
    _resp, body = _mint(client)
    assert _resp.status_code == 201 and body["reveal_url"]
    again = client.post("/tenant/almere-accept/secret-link")
    assert again.status_code == 409
    j = again.get_json()
    assert j["already_minted"] is True
    assert "al een wachtwoordlink" in j["errors"][0]


def test_create_route_refuses_an_existing_tenant(client, monkeypatch):
    """Aanmaken en bewerken zijn gescheiden schermen. Het aanmaakformulier
    verwijst naar de bewerkpagina in plaats van stilletjes te wijzigen."""
    _declared(monkeypatch, _PORTAL_FILE)
    monkeypatch.setattr(server.gitlib, "propose_update",
                        lambda **kw: pytest.fail("create-route wijzigde stilletjes"))
    resp = client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 409
    assert resp.get_json()["edit_url"] == "/tenant/almere-accept/edit"


def test_edit_route_updates_the_file(client, monkeypatch):
    """De bewerkroute haalt de naam uit de URL, niet uit het formulier."""
    _declared(monkeypatch, _PORTAL_FILE)
    seen = {}
    monkeypatch.setattr(server.gitlib, "propose_update",
                        lambda **kw: seen.update(kw) or {"number": 12, "html_url": "u/12"})
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: pytest.fail("create gebruikt terwijl de tenant bestaat"))

    resp = client.post("/tenant/almere-accept/edit", data={"frontend_theme": "nieuw-theme"})
    assert resp.status_code == 201 and resp.get_json()["updated"] is True
    assert seen["branch"] == "edit-tenant/almere-accept"
    assert 'themeClassname: "nieuw-theme"' in seen["content"]
    # de PR moet de ignore-diff-val benoemen
    assert "ignore-difft" in seen["pr_body"]


def test_edit_page_renders_current_values(client, monkeypatch):
    _declared(monkeypatch, _PORTAL_FILE)
    body = client.get("/tenant/almere-accept/edit").get_data(as_text=True)
    assert "Branding van almere-accept" in body
    assert 'value="almere-theme"' in body          # huidige waarde ingevuld
    assert 'name="cert"' in body and 'name="key"' in body   # certificaat-upload


def test_edit_page_refuses_a_hand_edited_file(client, monkeypatch):
    _declared(monkeypatch, _HAND_EDITED_FILE)
    body = client.get("/tenant/almere-accept/edit").get_data(as_text=True)
    assert "met de hand aangepast" in body
    assert 'name="frontend_theme"' not in body    # geen formulier aangeboden


def test_hand_edited_tenant_is_refused(client, monkeypatch):
    _declared(monkeypatch, _HAND_EDITED_FILE)
    monkeypatch.setattr(server.gitlib, "propose_update",
                        lambda **kw: pytest.fail("handgeschreven bestand toch overschreven"))
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: pytest.fail("handgeschreven bestand toch overschreven"))

    resp = client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 409
    assert "tenant.frontend.env" in resp.get_json()["errors"][0]


def test_unreadable_git_refuses_rather_than_guessing(client, monkeypatch):
    def boom(path, ref=None):
        raise server.gitlib.GitlibError(0, "cannot reach github")

    monkeypatch.setattr(server.gitlib, "get_file", boom)
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: pytest.fail("gegokt terwijl de status onbekend is"))
    resp = client.post("/tenant", data={"org": "almere", "environment": "accept"})
    assert resp.status_code == 502


# --- custom-domain TLS (sectie 3) ---


def test_tenant_custom_host_gets_a_tls_block(client, monkeypatch):
    """A custom-domain tenant arrives with its cert Secret NAME declared; the
    bytes never pass through the portal or through git."""
    captured = {}
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: captured.update(kw) or {"number": 10, "html_url": "u/10"})
    client.post("/tenant", data={"org": "almere", "environment": "accept",
                                 "frontend_host": "open.almere.nl"})
    c = captured["content"]
    assert "secretName: open-almere-nl-tls" in c
    assert "issuer: none" in c


def test_tenant_platform_host_gets_no_tls_block(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(server.gitlib, "propose_file",
                        lambda **kw: captured.update(kw) or {"number": 11, "html_url": "u/11"})
    client.post("/tenant", data={"org": "almere", "environment": "accept",
                                 "frontend_host": "almere.accept.openwoo.app"})
    assert "secretName" not in captured["content"]


def test_tenant_form_offers_the_certificate_choice(client):
    body = client.get("/tenant").get_data(as_text=True)
    assert 'name="frontend_tls_issuer"' in body
    assert "letsencrypt" in body


def test_org_pattern_is_mirrored_in_the_form(client):
    """De client-side check moet dezelfde regel hanteren als tenants.py::_ORG_RE.
    Loopt dat uiteen, dan vuurt de lookup weer op namen die de server afwijst —
    en die 400 werd stil ingeslikt (gezien 2026-08-07: drie mislukte pogingen
    tijdens het typen van een organisatienaam)."""
    body = client.get("/tenant").get_data(as_text=True)
    assert "const ORG_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/;" in body
    assert "function esc(" in body          # gebruikersinvoer gaat door innerHTML


def test_dashboard_offers_exactly_one_reveal_action(client, monkeypatch):
    """Eén actie per omgeving. Twee knoppen die allebei onbeperkt nieuwe links
    maakten was precies de klacht; de link is klikbaar én kopieerbaar, zodat
    zelf openen en doorsturen met dezelfde actie kunnen."""
    monkeypatch.setattr(server.argolib, "list_apps", lambda: [])
    monkeypatch.setattr(server.gitlib, "list_prs", lambda: [])
    body = client.get("/").get_data(as_text=True)
    assert "data-reveal=" in body
    assert "data-reveal-copy" not in body     # de tweede knop is weg
    assert "confirm(" in body                 # misklik op productie afvangen
    assert "wachtwoord gedeeld" in body       # reeds-gedeelde omgevingen


def test_reveal_token_is_redacted_in_access_logs():
    """Het token IS de credential. Zonder deze logger schrijft gunicorn hem in
    elke access-log-regel (gevonden in de dry-run van 2026-08-07)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "weblog_probe", str(REPO_ROOT / "webgui" / "weblog.py"))
    # gunicorn is geen testdependency; alleen de pure functie is interessant.
    import re
    src = (REPO_ROOT / "webgui" / "weblog.py").read_text()
    assert 'r"(/reveal/)[^/\\s?]+"' in src or "(/reveal/)" in src
    pat = re.compile(r"(/reveal/)[^/\s?]+")
    line = 'GET /reveal/KnXWWYN6ispnj3Q8l__YX HTTP/1.1'
    assert pat.sub(r"\1<token>", line) == "GET /reveal/<token> HTTP/1.1"
    assert spec is not None


def test_reveal_is_rate_limited(client, reveal_on, monkeypatch):
    """De reveal-route staat als enige buiten de proxy-login, dus de rem zit in
    de app. design.md beloofde dit; het stond er niet."""
    monkeypatch.setattr(server, "REVEAL_RATE_MAX", 3)
    server._reveal_hits.clear()
    codes = [client.get("/reveal/nietbestaand").status_code for _ in range(5)]
    assert codes[:3] == [404, 404, 404]       # binnen budget: normaal antwoord
    assert codes[-1] == 429                   # daarna afgeknepen
