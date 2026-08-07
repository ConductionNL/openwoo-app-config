# SPDX-License-Identifier: EUPL-1.2
# Tests for webgui/tenants.py — render + validation, stdlib only (no Flask needed).
"""Validate/render tests mirroring Nextcloud-base validate-values.sh rules."""

import sys
from pathlib import Path

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
