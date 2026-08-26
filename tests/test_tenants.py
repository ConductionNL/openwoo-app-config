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


def test_render_frontend_tag():
    out = tenants.render(_base(frontend_tag="dev"))
    assert "  frontend:" in out and '    tag: "dev"' in out
    # zonder tag geen frontend-blok als er verder niets is
    assert "frontend:" not in tenants.render(_base())


def test_frontend_tag_roundtrips():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(tenants.render(_base(frontend_tag="latest",
                                              frontend_org="Gemeente Almere")))
    assert tenants.unknown_keys(doc) == []
    assert tenants.from_declaration(doc)["frontend_tag"] == "latest"


def test_frontend_tag_is_no_longer_unknown():
    """De helft van de vloot pinde een frontend-versie; die bestanden waren
    daardoor niet bewerkbaar via de portal."""
    doc = {"tenant": {"name": "a-accept", "frontend": {"tag": "dev"}}}
    assert tenants.unknown_keys(doc) == []


def test_validate_rejects_full_image_reference_in_tag():
    """Het echte incident van 2026-08-11, twee keer: iemand plakte een volledige
    reference in het tag-veld. De ApplicationSet bouwt `<image>:<tag>`, dus dat
    rendert als `.../woo-website-v2:woo-website-v2:<tag>` en is ongeldig."""
    for bad in ("woo-website-v2:V1.0.260422-development",
                "docker.io/conduction2022/woo-website-v2:V1.0.260422-development",
                "conduction2022/woo-website-v2"):
        errors = tenants.validate(_base(frontend_tag=bad))
        assert errors, f"{bad!r} had geweigerd moeten worden"
        assert any("frontend.tag" in e for e in errors)
        assert any("alleen het tag-deel" in e for e in errors)


def test_validate_accepts_plain_tags():
    for good in ("latest", "dev", "V1.0.260422-development", "1.0.0-development.3"):
        assert tenants.validate(_base(frontend_tag=good)) == [], good


def test_render_registry_and_repository():
    out = tenants.render(_base(frontend_registry="docker.io",
                               frontend_repository="conduction2022/woo-website-v2",
                               frontend_tag="V1.0.260422-development"))
    assert '    registry: "docker.io"' in out
    assert '    repository: "conduction2022/woo-website-v2"' in out
    assert '    tag: "V1.0.260422-development"' in out


def test_registry_and_repository_roundtrip():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(tenants.render(_base(
        frontend_registry="ghcr.io",
        frontend_repository="conductionnl/woo-website-v2",
        frontend_tag="dev")))
    assert tenants.unknown_keys(doc) == []
    back = tenants.from_declaration(doc)
    assert back["frontend_registry"] == "ghcr.io"
    assert back["frontend_repository"] == "conductionnl/woo-website-v2"
    assert back["frontend_tag"] == "dev"


def test_validate_registry_requires_repository():
    """De ApplicationSet stelt de reference alleen samen als er een repository is;
    een registry op zichzelf wordt stil genegeerd."""
    errors = tenants.validate(_base(frontend_registry="docker.io"))
    assert any("zonder frontend.repository" in e for e in errors)


def test_validate_rejects_misplaced_image_parts():
    with_tag = tenants.validate(_base(
        frontend_registry="docker.io",
        frontend_repository="conduction2022/woo-website-v2:V1"))
    assert any("repository" in e and "':'" in e for e in with_tag)

    with_path = tenants.validate(_base(
        frontend_registry="docker.io/conduction2022",
        frontend_repository="woo-website-v2"))
    assert any("registry" in e and "'/'" in e for e in with_path)


def test_validate_accepts_full_image_triple():
    assert tenants.validate(_base(
        frontend_registry="docker.io",
        frontend_repository="conduction2022/woo-website-v2",
        frontend_tag="V1.0.260422-development")) == []


def test_validate_rejects_thema_typo():
    """`-thema` in plaats van `-theme` geeft geen foutmelding maar een site zonder
    huisstijl: de klasse bestaat niet in de bundle. Stond live op twee tenants."""
    errors = tenants.validate(_base(frontend_theme="noordwijk-thema"))
    assert any("themeClassname" in e for e in errors)
    assert any("-thema" in e for e in errors)


def test_validate_accepts_valid_themes():
    for good in ("epe-theme", "hof-van-twente-theme", "conduction-theme"):
        assert tenants.validate(_base(frontend_theme=good)) == [], good


def test_unknown_keys_flags_hand_written_fields():
    """The live fleet carries keys the form does not model; those files must not
    be re-rendered by the portal."""
    doc = {"tenant": {"name": "almere-accept", "environment": "accept",
                      "hostnameOverride": True,
                      "frontend": {"env": {"X": "1"}, "host": "open.almere.nl"}},
           "resources": {"limits": {}}}
    assert tenants.unknown_keys(doc) == [
        "resources", "tenant.frontend.env", "tenant.hostnameOverride"]


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


# --- Nextcloud image-override -------------------------------------------------
# De regel die hier wordt afgedwongen komt uit Nextcloud-base
# docs/ADDING-TENANT.md: nooit een lagere versie dan wat de tenant draait.


def test_image_version_parses_only_versioned_tags():
    assert tenants.image_version("32.0.6-fpm-soap") == (32, 0, 6)
    assert tenants.image_version("32.0.13-fpm") == (32, 0, 13)
    assert tenants.image_version("32.0.6") == (32, 0, 6)
    # Zwevende tags dragen geen versie; daarop rust de weigering ervan.
    assert tenants.image_version("fpm-soap") is None
    assert tenants.image_version("latest") is None
    assert tenants.image_version("") is None
    assert tenants.image_version(None) is None


def test_compare_versions_beats_the_string_comparison_trap():
    """Lexicaal is '32.0.6-...' GROTER dan '32.0.13-...', want '6' > '1'.

    Een stringvergelijking concludeert dus dat 32.0.6 nieuwer is dan 32.0.13 en
    laat exact de downgrade door die de guard moet vangen. Deze test pint de
    juiste richting vast.
    """
    assert "32.0.6-fpm-soap" > "32.0.13-fpm"          # de val, als tekst
    assert tenants.compare_versions("32.0.6-fpm-soap", "32.0.13-fpm") == -1
    assert tenants.compare_versions("32.0.13-fpm", "32.0.6-fpm-soap") == 1


def test_compare_versions_ignores_the_build_suffix():
    """Zelfde versie, andere build: geen downgrade, dus toegestaan."""
    assert tenants.compare_versions("32.0.6-fpm", "32.0.6-fpm-soap") == 0
    # Onvergelijkbaar zodra een kant geen versie draagt.
    assert tenants.compare_versions("fpm-soap", "32.0.13-fpm") is None


def test_render_emits_a_top_level_image_block():
    """Top-level, NIET onder `tenant:` — het is een chart-value."""
    f = tenants.from_org("almere", "accept",
                         nc_registry="ghcr.io",
                         nc_repository="conductionnl/nextcloud-images",
                         nc_tag="32.0.6-fpm-soap")
    out = tenants.render(f)
    assert tenants.validate(f) == []
    assert "\nimage:\n" in out
    assert '  tag: "32.0.6-fpm-soap"' in out
    # Geen digest-veld: chart 8.9.0 rendert het niet, dus git zou iets beweren
    # wat de podspec niet doet.
    assert "digest" not in out


def test_render_omits_the_image_block_when_unset():
    assert "image:" not in tenants.render(tenants.from_org("almere", "accept"))


def test_image_override_round_trips():
    """render -> parse -> from_declaration -> render moet identiek zijn.

    Zonder dit is de override niet beheerbaar: het portaal zou het blok bij het
    opslaan stil weggooien.
    """
    f = tenants.from_org("almere", "accept",
                         nc_registry="ghcr.io",
                         nc_repository="conductionnl/nextcloud-images",
                         nc_tag="32.0.6-fpm-soap")
    first = tenants.render(f)
    doc = {"tenant": {"name": "almere-accept", "environment": "accept",
                      "wave": "1", "dbType": "postgres",
                      "secrets": {"managed": True},
                      "apps": {"enabled": list(tenants.KNOWN_APPS)},
                      "frontend": {"branding": {"organisationName": "Gemeente Almere"}}},
           "image": {"registry": "ghcr.io",
                     "repository": "conductionnl/nextcloud-images",
                     "tag": "32.0.6-fpm-soap"}}
    assert tenants.unknown_keys(doc) == []
    assert tenants.render(tenants.from_declaration(doc)) == first


def test_validate_rejects_a_floating_nextcloud_tag():
    """`fpm-soap` is een geldige tag maar een ongeldige keuze.

    Met pullPolicy IfNotPresent hangt de draaiende versie af van wanneer een node
    voor het laatst pullde: op 2026-08-19 schoof `fpm-soap` van sha256:31123c8c
    naar sha256:80310a36 zonder wijziging in git.
    """
    f = tenants.from_org("almere", "accept",
                         nc_registry="ghcr.io",
                         nc_repository="conductionnl/nextcloud-images",
                         nc_tag="fpm-soap")
    errors = tenants.validate(f)
    assert any("geen versienummer" in e for e in errors), errors
    # De frontend-tag houdt de laksere regel: die tags dragen geen semver.
    g = tenants.from_org("almere", "accept",
                         registry="docker.io", repository="conduction2022/woo-website-v2",
                         tag="V1.0.260422-development")
    assert tenants.validate(g) == []


def test_validate_rejects_a_full_reference_in_the_image_tag():
    f = tenants.from_org("almere", "accept", nc_tag="ghcr.io/x/y:32.0.6-fpm")
    assert any("volledige image-reference" in e for e in tenants.validate(f))


def test_validate_rejects_registry_without_repository():
    f = tenants.from_org("almere", "accept", nc_registry="ghcr.io", nc_tag="32.0.6-fpm")
    assert any("zonder image.repository" in e for e in tenants.validate(f))


def test_unknown_keys_accepts_versions_now_that_it_is_rendered():
    """`apps.versions` wordt sinds 2026-08-26 wél gerenderd, dus is het geen
    reden meer om een bestand read-only te zetten.

    Deze test hield eerder het omgekeerde vast (`== ["tenant.apps.versions"]`).
    Dat was correct zolang render() de pins niet emitte: dan zou opslaan ze stil
    weggooien. Nu ze wél worden geemit, zou read-only blijven de tenant zonder
    reden buiten het portaal houden.
    """
    doc = {"tenant": {"name": "almere-accept", "environment": "accept",
                      "dbType": "postgres",
                      "apps": {"enabled": list(tenants.KNOWN_APPS),
                               "versions": {"opencatalogi": "0.7.12"}}},
           "image": {"registry": "ghcr.io",
                     "repository": "conductionnl/nextcloud-images",
                     "tag": "32.0.6-fpm-soap"}}
    assert tenants.unknown_keys(doc) == []


def test_unknown_keys_still_guards_anything_else_inside_apps():
    """De afdaling in `apps` blijft nodig voor wat render() níet kent."""
    doc = {"tenant": {"name": "almere-accept", "environment": "accept",
                      "dbType": "postgres",
                      "apps": {"enabled": list(tenants.KNOWN_APPS),
                               "disabled": ["iets"]}}}
    assert tenants.unknown_keys(doc) == ["tenant.apps.disabled"]


# --- per-app versie-pins ------------------------------------------------------


def test_render_emits_app_version_pins():
    f = tenants.from_org("almere", "accept",
                         versions={"opencatalogi": "0.7.12", "openregister": "0.2.11"})
    out = tenants.render(f)
    assert tenants.validate(f) == []
    assert "    versions:\n" in out
    assert '      opencatalogi: "0.7.12"' in out
    assert '      openregister: "0.2.11"' in out
    # Niet gepinde apps krijgen geen key: die volgen de laatste release, en de
    # ApplicationSet geeft dan "" mee.
    assert "openconnector:" not in out


def test_render_omits_the_versions_block_when_nothing_is_pinned():
    assert "versions:" not in tenants.render(tenants.from_org("almere", "accept"))


def test_validate_rejects_a_leading_v_with_its_own_message():
    """De klassieker: GitHub-releases heten `v0.7.12`, het veld wil het zonder."""
    f = tenants.from_org("almere", "accept", versions={"opencatalogi": "v0.7.12"})
    errors = tenants.validate(f)
    assert any("mag niet met 'v' beginnen" in e for e in errors), errors
    assert any("0.7.12" in e for e in errors)      # noemt de juiste waarde


def test_validate_rejects_incomplete_and_floating_app_versions():
    for bad in ("0.7", "0.7.x", "latest", "1"):
        f = tenants.from_org("almere", "accept", versions={"openconnector": bad})
        assert tenants.validate(f), f"{bad} had moeten falen"


def test_validate_accepts_the_suffix_forms_the_shell_validator_accepts():
    """Spiegelt de voorbeelden uit validate_app_versions_format()."""
    for good in ("0.7.12", "0.2.8-beta.7", "0.2.10-unstable.4",
                 "0.2.12-beta.20260410072957"):
        f = tenants.from_org("almere", "accept", versions={"openregister": good})
        assert tenants.validate(f) == [], f"{good} had moeten passeren"


def test_a_file_with_both_image_and_pins_round_trips_without_loss():
    """Het geval dat deze change nodig maakte.

    Dit is de inhoud van het tenantbestand dat op 2026-08-26 met de hand werd
    geschreven: een top-level `image:`-blok EN `apps.versions`. Het was daardoor
    in het portaal niet te beheren.

    Semantisch vergelijken, niet byte-identiek: het echte bestand draagt
    commentaarregels en render() emit die niet. De eigenschap die telt is dat
    geen sleutel of waarde verdwijnt — anders kleedt opslaan het bestand stil uit.
    """
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load("""---
tenant:
  name: myorg-accept
  environment: accept
  wave: "1"
  dbType: mariadb
  secrets:
    managed: true
  apps:
    enabled:
      - opencatalogi
      - openconnector
      - openregister
    # BCT Woo versions
    versions:
      opencatalogi: "0.7.12"
      openconnector: "0.2.19"
      openregister: "0.2.11"
  frontend:
    branding:
      organisationName: "Gemeente Myorg"

image:
  registry: ghcr.io
  repository: conductionnl/nextcloud-images
  tag: "32.0.6-fpm-soap"
""")
    # Alles wat erin staat wordt geemit, dus beheerbaar.
    assert tenants.unknown_keys(doc) == []

    fields = tenants.from_declaration(doc)
    assert tenants.validate(fields) == []
    again = yaml.safe_load(tenants.render(fields))

    assert again["image"] == doc["image"]
    assert again["tenant"]["apps"]["versions"] == doc["tenant"]["apps"]["versions"]
    assert again["tenant"]["apps"]["enabled"] == doc["tenant"]["apps"]["enabled"]
    assert again["tenant"]["dbType"] == "mariadb"
    assert again["tenant"]["secrets"] == {"managed": True}
    # Nog een ronde levert exact hetzelfde op: stabiel, geen drift bij herhaald
    # opslaan.
    assert tenants.render(tenants.from_declaration(again)) == tenants.render(fields)
