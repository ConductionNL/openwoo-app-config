# SPDX-License-Identifier: EUPL-1.2
# Tests for webgui/tenants.py — render + validation, stdlib only (no Flask needed).
"""Validate/render tests mirroring Nextcloud-base validate-values.sh rules."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import tenants  # noqa: E402


def _base(**over):
    f = {"name": "almere-accept", "environment": "accept", "dbType": "postgres",
         "apps": ["opencatalogi"]}
    f.update(over)
    return f


def test_valid_tenant_has_no_errors():
    assert tenants.validate(_base()) == []


def test_name_must_have_env_suffix():
    errs = tenants.validate(_base(name="almere"))
    assert any("org" in e for e in errs)


def test_environment_must_match_suffix():
    assert tenants.validate(_base(name="almere-prod", environment="accept"))
    assert tenants.validate(_base(name="almere-accept", environment="prod"))


def test_test_and_demo_suffix_map_to_accept():
    assert tenants.validate(_base(name="almere-test", environment="accept")) == []
    assert tenants.validate(_base(name="almere-demo", environment="accept")) == []
    # ...but prod env on a -test name is rejected
    assert tenants.validate(_base(name="almere-test", environment="prod"))


def test_bad_dbtype_rejected():
    assert tenants.validate(_base(dbType="sqlite"))


def test_no_apps_rejected():
    assert tenants.validate(_base(apps=[]))


def test_unknown_app_rejected():
    assert tenants.validate(_base(apps=["opencatalogi", "bogusapp"]))


def test_render_minimal():
    out = tenants.render(_base(apps=["opencatalogi", "openregister"]))
    assert out.startswith("---\n")
    assert "  name: almere-accept" in out
    assert "  environment: accept" in out
    assert '  wave: "1"' in out
    assert "  dbType: postgres" in out
    assert "      - opencatalogi" in out and "      - openregister" in out
    # new-world tenants are ESO-managed
    assert "  secrets:" in out and "    managed: true" in out
    # no frontend block when host/org absent
    assert "frontend:" not in out


def test_render_with_frontend_block():
    out = tenants.render(_base(frontend_host="open.almere.nl",
                               frontend_org="Gemeente Almere"))
    assert "  frontend:" in out
    assert "    host: open.almere.nl" in out
    assert '      organisationName: "Gemeente Almere"' in out


def test_render_branding_extras():
    """themeClassname/jumbotron/favicon land under branding, in the shape the
    react-tenants ApplicationSet reads (cf. tenant-tubbergen-accept.yaml)."""
    out = tenants.render(_base(frontend_org="Gemeente Almere",
                               frontend_theme="almere-theme",
                               frontend_jumbotron="https://ex.org/jumbotron.jpg",
                               frontend_favicon="https://ex.org/favicon.ico"))
    assert '      organisationName: "Gemeente Almere"' in out
    assert '      themeClassname: "almere-theme"' in out
    assert '      jumbotronImageUrl: "https://ex.org/jumbotron.jpg"' in out
    assert '      faviconUrl: "https://ex.org/favicon.ico"' in out


def test_render_blank_theme_is_omitted_so_the_baseline_applies():
    """A blank theme must NOT be emitted: the ApplicationSet then falls back to
    `conduction-theme`, which renders. Emitting an empty or derived `<org>-theme`
    is the 2026-06-30 bug where onboarded tenants rendered without a theme."""
    out = tenants.render(_base(frontend_org="Gemeente Almere", frontend_theme="  "))
    assert "themeClassname" not in out


def test_render_branding_extra_without_organisation_name():
    """A branding extra on its own still opens the frontend/branding blocks."""
    out = tenants.render(_base(frontend_theme="almere-theme"))
    assert "  frontend:" in out and "    branding:" in out
    assert '      themeClassname: "almere-theme"' in out
    assert "organisationName" not in out


def test_from_org_leaves_branding_extras_blank_by_default():
    f = tenants.from_org("almere", "accept")
    assert f["frontend_theme"] == ""
    assert f["frontend_jumbotron"] == "" and f["frontend_favicon"] == ""


def test_from_org_passes_branding_extras_through():
    f = tenants.from_org("almere", "accept", theme=" almere-theme ",
                         jumbotron="https://ex.org/j.jpg", favicon="https://ex.org/f.ico")
    assert f["frontend_theme"] == "almere-theme"          # trimmed
    assert f["frontend_jumbotron"] == "https://ex.org/j.jpg"
    assert f["frontend_favicon"] == "https://ex.org/f.ico"


def test_custom_frontend_host_detection():
    assert not tenants.is_custom_frontend_host("")            # derived -> wildcard
    assert not tenants.is_custom_frontend_host("almere.accept.openwoo.app")
    assert not tenants.is_custom_frontend_host("openwoo.app")
    assert tenants.is_custom_frontend_host("open.almere.nl")
    # a lookalike suffix must NOT count as the platform domain
    assert tenants.is_custom_frontend_host("evilopenwoo.app")


def test_tls_secret_name_follows_the_fleet_convention():
    # exactly the shape of tenant-oudeijsselstreek-accept.yaml
    assert tenants.tls_secret_name("acceptatie-open.oude-ijsselstreek.nl") == \
        "acceptatie-open-oude-ijsselstreek-nl-tls"
    assert tenants.tls_secret_name("open.almere.nl") == "open-almere-nl-tls"
    assert tenants.tls_secret_name("OPEN.Almere.NL") == "open-almere-nl-tls"
    assert tenants.tls_secret_name("open.almere.nl.") == "open-almere-nl-tls"


def test_render_emits_tls_for_a_custom_host():
    out = tenants.render(_base(frontend_host="open.almere.nl"))
    assert "    tls:" in out
    assert "      secretName: open-almere-nl-tls" in out
    assert "      issuer: none" in out          # BYO is the default


def test_render_tls_issuer_can_be_letsencrypt():
    out = tenants.render(_base(frontend_host="open.almere.nl",
                               frontend_tls_issuer="letsencrypt-prod"))
    assert "      issuer: letsencrypt-prod" in out


def test_render_omits_tls_on_the_platform_domain():
    """The wildcard already covers it; a per-tenant block would point the
    Ingress at a Secret nobody created."""
    out = tenants.render(_base(frontend_host="almere.accept.openwoo.app"))
    assert "tls:" not in out
    assert "secretName" not in out


def test_render_omits_tls_without_a_host():
    assert "tls:" not in tenants.render(_base(frontend_org="Gemeente Almere"))


def test_from_org_defaults_the_issuer_to_byo():
    assert tenants.from_org("almere", "accept")["frontend_tls_issuer"] == "none"
    assert tenants.from_org("almere", "accept",
                            tls_issuer="letsencrypt-prod")["frontend_tls_issuer"] == \
        "letsencrypt-prod"


def test_unknown_tls_issuer_rejected():
    assert tenants.validate(_base(frontend_tls_issuer="letsencrypt-staging"))
    assert not tenants.validate(_base(frontend_tls_issuer="none"))


def test_render_roundtrips_through_from_declaration():
    """What render() writes, from_declaration() must read back — otherwise the
    edit-flow silently changes values the operator never touched."""
    yaml = pytest.importorskip("yaml")
    fields = _base(frontend_host="open.almere.nl", frontend_org="Gemeente Almere",
                   frontend_theme="almere-theme",
                   frontend_jumbotron="https://ex.org/j.jpg",
                   frontend_favicon="https://ex.org/f.ico",
                   frontend_tls_issuer="none")
    back = tenants.from_declaration(yaml.safe_load(tenants.render(fields)))
    for key in ("name", "environment", "dbType", "frontend_host", "frontend_org",
                "frontend_theme", "frontend_jumbotron", "frontend_favicon",
                "frontend_tls_issuer"):
        assert back[key] == fields[key], key
    assert back["apps"] == fields["apps"]


def test_rendered_file_has_no_unknown_keys():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(tenants.render(_base(frontend_host="open.almere.nl",
                                              frontend_org="Gemeente Almere")))
    assert tenants.unknown_keys(doc) == []


def test_unknown_keys_flags_hand_written_fields():
    """The live fleet carries keys the form does not model; those files must not
    be re-rendered by the portal."""
    doc = {"tenant": {"name": "almere-accept", "environment": "accept",
                      "hostnameOverride": True,
                      "frontend": {"tag": "dev", "host": "open.almere.nl"}},
           "resources": {"limits": {}}}
    assert tenants.unknown_keys(doc) == [
        "resources", "tenant.frontend.tag", "tenant.hostnameOverride"]


def test_unknown_keys_on_junk_input():
    assert tenants.unknown_keys(None) == ["<geen geldig tenantbestand>"]
    assert tenants.unknown_keys("nope") == ["<geen geldig tenantbestand>"]


def test_from_declaration_defaults_a_missing_issuer():
    doc = {"tenant": {"name": "a-accept", "environment": "accept",
                      "frontend": {"host": "open.a.nl"}}}
    assert tenants.from_declaration(doc)["frontend_tls_issuer"] == tenants.DEFAULT_TLS_ISSUER


def test_render_quotes_escape():
    out = tenants.render(_base(frontend_org='He said "hi"'))
    assert r'\"hi\"' in out


def test_filename():
    assert tenants.filename("almere-accept") == "tenant-almere-accept.yaml"


# --- minimal-input derivation (org + environment) ---

def test_org_display_defaults_to_gemeente():
    assert tenants.org_display("almere") == "Gemeente Almere"
    assert tenants.org_display("oude-ijsselstreek") == "Gemeente Oude Ijsselstreek"


def test_validate_org_rejects_full_name_and_bad_chars():
    assert tenants.validate_org("almere-accept", "accept")  # has env suffix
    assert tenants.validate_org("Almere", "accept")          # uppercase
    assert tenants.validate_org("", "accept")                # empty
    assert tenants.validate_org("almere", "staging")         # bad env
    assert tenants.validate_org("almere", "accept") == []    # ok


def test_from_org_derives_full_fields():
    f = tenants.from_org("almere", "accept")
    assert f["name"] == "almere-accept"
    assert f["dbType"] == "postgres"
    assert f["apps"] == list(tenants.KNOWN_APPS)
    assert f["frontend_org"] == "Gemeente Almere"
    assert f["frontend_host"] == ""
    # derived fields pass the full validator
    assert tenants.validate(f) == []


def test_from_org_honours_overrides():
    f = tenants.from_org("almere", "prod", dbType="mariadb",
                         display="Provincie X", host="open.almere.nl")
    assert f["name"] == "almere-prod" and f["dbType"] == "mariadb"
    assert f["frontend_org"] == "Provincie X" and f["frontend_host"] == "open.almere.nl"
