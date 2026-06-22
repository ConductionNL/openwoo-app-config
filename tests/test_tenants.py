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
