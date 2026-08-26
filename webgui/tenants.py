#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/tenants.py — render + validate a Nextcloud-base tenant file from form
# input, with NO third-party YAML dependency (the file is emitted as text).
#
# The validation mirrors Nextcloud-base `scripts/validate-values.sh` so the
# portal never opens a PR that the repo's CI would reject: name must be
# `<org>-<accept|test|demo|prod>`, environment must match the suffix, dbType is
# one of mariadb|postgres|external, and at least one app is enabled. Keeping the
# rules in lockstep with the validator is the contract (see that script).
#
# Writes: read-only (pure functions returning strings/lists).
# Requires: python3.8+ (stdlib `re` only).
"""Pure render/validate helpers for Nextcloud-base tenant files."""

import re

ENVS = ("accept", "prod")
DB_TYPES = ("mariadb", "postgres", "external")
KNOWN_APPS = ("opencatalogi", "openconnector", "openregister")

# Apps waarvan een versie-pin daadwerkelijk aankomt. De ApplicationSet mapt
# precies deze drie naar OPENCATALOGI_VERSION / OPENCONNECTOR_VERSION /
# OPENREGISTER_VERSION (argo/applicationsets/nextcloud-tenants.yaml). Een pin op
# een andere naam passeert `validate-values.sh` — die heeft geen allowlist — en
# doet vervolgens niets. In een formulier is zo'n stille no-op erger dan een
# foutmelding, dus hier weigeren we het wél. Een vierde pinbare app betekent:
# eerst de ApplicationSet, dan deze tuple.
PINNABLE_APPS = ("opencatalogi", "openconnector", "openregister")

# Spiegelt `ver_re` in validate_app_versions_format() van validate-values.sh:
# drie numerieke delen, optioneel suffix na '-' of '.', geen leidende 'v'.
_APP_VERSION_RE = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+([-.][0-9A-Za-z][0-9A-Za-z.-]*)?$")

# Frontend hosts on the platform domain are covered by the shared wildcard cert
# (`wildcard-openwoo-tls`, the ApplicationSet's default). Only a host OUTSIDE it
# needs a per-tenant `frontend.tls` block — emitting one for a platform host
# would replace a working wildcard with a secret nobody created.
PLATFORM_FRONTEND_DOMAIN = "openwoo.app"

# How a custom-domain frontend gets its certificate.
#   none            — bring your own: no cert-manager annotation, no Certificate
#                     object, so nothing can overwrite a customer-supplied cert.
#                     An operator seeds the Secret out of band (docs/custom-domain-cert.md).
#   letsencrypt-prod — cert-manager issues per host over HTTP-01.
# The ApplicationSet treats "none" and "absent" identically (it only writes the
# annotation `if and $tlsIssuer (ne $tlsIssuer "none")`); we still write "none"
# explicitly, because "nobody has decided yet" and "we deliberately bring our
# own" must not look the same in a tenant file.
TLS_ISSUERS = ("none", "letsencrypt-prod")
DEFAULT_TLS_ISSUER = "none"

# `<org>-<suffix>` with org a valid k8s-ish name segment. Matches the
# validate-values.sh convention (suffixes accept|test|demo|prod; test/demo -> accept).
_NAME_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])-(accept|test|demo|prod)$")
_SUFFIX_ENV = {"prod": "prod", "accept": "accept", "test": "accept", "demo": "accept"}

# Alleen het TAG-deel van een image-reference. De react-tenants ApplicationSet
# bouwt de image als `<pwa.image.image>:<pwa.image.tag>`, dus een volledige
# reference in dit veld rendert als
# `docker.io/conduction2022/woo-website-v2:woo-website-v2:V1.0.260422-development`
# en is ongeldig. Dat is twee keer gebeurd op 2026-08-11 (epe-accept en
# tubbergen-prod): iemand plakte `woo-website-v2:<tag>` uit een registry-UI in
# een vrij tekstveld en niets ving het. Nextcloud-base CI ving het pas ná de
# merge op main.
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

# Het versiedeel van een Nextcloud-image-tag: `32.0.6-fpm-soap` -> 32.0.6.
# Bewust alleen aan het begin van de tag en met alle drie de delen verplicht:
# een tag zonder versie (`fpm-soap`, `latest`) mag geen versie opleveren, want
# daarop rust de weigering van zwevende tags.
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-].*)?$")

# Het pad-deel, zonder registry-host en zonder tag. Bijvoorbeeld
# `conduction2022/woo-website-v2`.
_REPOSITORY_RE = re.compile(r"^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$")

# Alleen de host, met optionele poort. Bijvoorbeeld `docker.io`, `ghcr.io` of
# `registry.local:5000`. Een pad hoort in repository.
_REGISTRY_RE = re.compile(r"^[a-z0-9]+([.-][a-z0-9]+)*(:[0-9]+)?$")

# NL Design-themaklasse. De geldige thema's staan in ConductionNL/conduction-theme
# (map `<naam>-design-tokens` -> klasse `<naam>-theme`) en worden in het image
# gebundeld. Die lijst wijzigt vaak en het image loopt erop achter, dus toetsen we
# alleen de VORM — net als validate-values.sh. Vangt `-thema` i.p.v. `-theme`,
# wat stilzwijgend geen thema oplevert omdat de waarde ongewijzigd doorgaat naar
# GATSBY_NL_DESIGN_THEME_CLASSNAME.
_THEME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-theme$")


def filename(name):
    """Repo-relative path for a tenant's values file."""
    return f"tenant-{name}.yaml"


_ORG_RE = re.compile(r"^([a-z][a-z0-9-]*[a-z0-9]|[a-z])$")
_RESERVED_SUFFIX = re.compile(r"-(accept|test|demo|prod)$")


def env_for_suffix(suffix):
    """De omgeving die bij een naamsuffix hoort ('test'/'demo' tellen als accept).

    De bewerkroute kent alleen de tenantnaam en moet daaruit dezelfde
    environment afleiden die validate() verwacht.
    """
    return _SUFFIX_ENV.get((suffix or "").strip(), "")


def is_custom_frontend_host(host):
    """True when `host` needs its own certificate.

    A blank host means the platform derives `<org>.<env>.openwoo.app`, which the
    wildcard already covers — so blank is never custom.
    """
    host = (host or "").strip().lower()
    if not host:
        return False
    return not (host == PLATFORM_FRONTEND_DOMAIN
                or host.endswith("." + PLATFORM_FRONTEND_DOMAIN))


def tls_secret_name(host):
    """Derive the TLS Secret name for a custom frontend host.

    Follows the convention already in the fleet rather than inventing one:
    `acceptatie-open.oude-ijsselstreek.nl` -> `acceptatie-open-oude-ijsselstreek-nl-tls`
    (see Nextcloud-base tenant-oudeijsselstreek-accept.yaml). Dots become
    dashes; the name is a DNS-1123 label per Kubernetes' Secret naming rules.
    """
    host = (host or "").strip().lower().rstrip(".")
    return re.sub(r"[^a-z0-9-]", "-", host).strip("-") + "-tls"


def org_display(org):
    """Default WOO branding name (PO convention): 'Gemeente <Org>'."""
    parts = [p for p in (org or "").replace("_", "-").split("-") if p]
    return "Gemeente " + " ".join(p.capitalize() for p in parts)


def validate_org(org, environment):
    """Validate the minimal operator input (bare org + environment). The full
    tenant name is DERIVED (`<org>-<env>`), so the operator never types it."""
    org = (org or "").strip()
    errs = []
    if not org:
        errs.append("organisation is required")
    elif _RESERVED_SUFFIX.search(org):
        errs.append("give the bare organisation (e.g. 'almere'), not '<org>-<env>' "
                    "— the environment is the dropdown")
    elif not _ORG_RE.match(org):
        errs.append("organisation must be lowercase letters/digits/hyphens "
                    "(e.g. 'almere', 'oude-ijsselstreek')")
    if environment not in ENVS:
        errs.append(f"environment must be one of {ENVS}")
    return errs


def from_org(org, environment, dbType=None, display=None, host=None,
             theme=None, jumbotron=None, favicon=None, tls_issuer=None, tag=None,
             registry=None, repository=None,
             nc_tag=None, nc_registry=None, nc_repository=None,
             versions=None):
    """Build the full fields dict from the minimal input. Everything not given is
    derived: name=`<org>-<env>`, all three apps, branding 'Gemeente <Org>',
    db=postgres, host blank (=> platform derives <org>.<env>.commonground.nu).

    The branding extras (theme/jumbotron/favicon) default to blank on purpose —
    see render() for why a blank theme is the safe value."""
    org = (org or "").strip().lower()
    env = (environment or "").strip()
    disp = (display or "").strip() or (org_display(org) if org else "")
    return {
        "name": f"{org}-{env}" if (org and env) else "",
        "environment": env,
        "dbType": (dbType or "").strip() or "postgres",
        "wave": "1",
        "apps": list(KNOWN_APPS),
        "frontend_org": disp,
        "frontend_host": (host or "").strip(),
        "frontend_theme": (theme or "").strip(),
        "frontend_jumbotron": (jumbotron or "").strip(),
        "frontend_favicon": (favicon or "").strip(),
        "frontend_tls_issuer": (tls_issuer or "").strip() or DEFAULT_TLS_ISSUER,
        "frontend_tag": (tag or "").strip(),
        "frontend_registry": (registry or "").strip(),
        "frontend_repository": (repository or "").strip(),
        # Per-app versie-pins. Leeg = de app volgt de laatste release; de
        # ApplicationSet geeft dan `""` mee. Alleen PINNABLE_APPS komen aan.
        **{f"version_{app}": str((versions or {}).get(app) or "").strip()
           for app in PINNABLE_APPS},
        # Top-level `image:` — de Nextcloud-image zelf, niet de frontend. Leeg
        # betekent: volg de platformstandaard uit common.yaml. Zie render().
        "nc_image_tag": (nc_tag or "").strip(),
        "nc_image_registry": (nc_registry or "").strip(),
        "nc_image_repository": (nc_repository or "").strip(),
    }


# Exactly what render() emits, nothing else. The portal may only rewrite a file
# it could have written itself: re-rendering a file that carries anything outside
# this set would silently drop that key. Measured on the live fleet (2026-08-07),
# hand-written tenant files carry `frontend.tag` (24×), `hostname`/
# `hostnameOverride` (7/6), `namespace` (6) and more — none of which the form
# models. Those files are hand-managed and stay that way.
# `image` staat er bij sinds de image-override-change: zonder die key meldt
# unknown_keys() elke tenant met een eigen Nextcloud-image als hand-geschreven,
# en zet _declaration() hem read-only. Dat kostte precies de tenants die het
# portaal het hardst nodig hebben (harderwijk-prod, rijswijk-accept) hun
# beheerbaarheid.
RENDERED_TOP_KEYS = frozenset({"tenant", "image"})
RENDERED_TENANT_KEYS = frozenset({"name", "environment", "wave", "dbType",
                                  "secrets", "apps", "frontend"})
# Binnen `apps` rendert render() `enabled` en `versions`. Alles daarbuiten meldt
# unknown_keys(), waardoor zo'n bestand read-only blijft in plaats van bij het
# opslaan stil te worden uitgekleed.
RENDERED_APPS_KEYS = frozenset({"enabled", "versions"})
RENDERED_FRONTEND_KEYS = frozenset({"tag", "registry", "repository", "host",
                                    "tls", "branding"})
RENDERED_BRANDING_KEYS = frozenset({"organisationName", "themeClassname",
                                    "jumbotronImageUrl", "faviconUrl"})
RENDERED_TLS_KEYS = frozenset({"secretName", "issuer"})


def unknown_keys(doc):
    """Keys in a parsed tenant file that render() would not emit.

    Empty list == the portal wrote this file (or could have), so re-rendering it
    is lossless and it is safe to offer as editable. A non-empty list means
    somebody hand-edited it; the portal must then show it read-only rather than
    quietly discard their work. Returns dotted paths, sorted, for display.
    """
    if not isinstance(doc, dict):
        return ["<geen geldig tenantbestand>"]
    found = []

    def check(mapping, allowed, prefix):
        if not isinstance(mapping, dict):
            return
        found.extend(f"{prefix}{k}" for k in mapping if k not in allowed)

    check(doc, RENDERED_TOP_KEYS, "")
    tenant = doc.get("tenant") or {}
    check(tenant, RENDERED_TENANT_KEYS, "tenant.")
    frontend = tenant.get("frontend") or {}
    check(frontend, RENDERED_FRONTEND_KEYS, "tenant.frontend.")
    check(frontend.get("branding") or {}, RENDERED_BRANDING_KEYS, "tenant.frontend.branding.")
    check(frontend.get("tls") or {}, RENDERED_TLS_KEYS, "tenant.frontend.tls.")
    # `secrets` is emitted as exactly {managed: true}; anything else is not ours.
    secrets = tenant.get("secrets")
    if isinstance(secrets, dict):
        check(secrets, frozenset({"managed"}), "tenant.secrets.")
    # `apps` too: render() emits only `enabled`. A tenant file may also carry
    # `apps.versions` (per-app pins, documented in Nextcloud-base
    # docs/ADDING-TENANT.md) and re-rendering would silently drop those.
    # Descending here is what keeps such a file read-only instead of quietly
    # losing three version pins — the exact loss `image` used to mask on
    # tenant-harderwijk-prod.yaml before `image` became a rendered key.
    apps = tenant.get("apps")
    if isinstance(apps, dict):
        check(apps, RENDERED_APPS_KEYS, "tenant.apps.")
    return sorted(found)


def from_declaration(doc):
    """Turn a parsed tenant file back into the form's fields dict.

    The inverse of render() for the subset the form owns. Caller parses the
    YAML (this module stays dependency-free on purpose) and should check
    unknown_keys() first — this function ignores anything it does not model,
    which is exactly what makes that check necessary.
    """
    tenant = (doc or {}).get("tenant") or {}
    frontend = tenant.get("frontend") or {}
    branding = frontend.get("branding") or {}
    tls = frontend.get("tls") or {}
    # Top-level, naast `tenant:` — zie de toelichting in render().
    nc_image = (doc or {}).get("image") or {}
    pins = (tenant.get("apps", {}) or {}).get("versions") or {}
    out = {f"version_{app}": str(pins.get(app) or "") for app in PINNABLE_APPS}
    return {
        **out,
        "nc_image_registry": str(nc_image.get("registry") or ""),
        "nc_image_repository": str(nc_image.get("repository") or ""),
        "nc_image_tag": str(nc_image.get("tag") or ""),
        "name": str(tenant.get("name") or ""),
        "environment": str(tenant.get("environment") or ""),
        "wave": str(tenant.get("wave") or "1"),
        "dbType": str(tenant.get("dbType") or "postgres"),
        "apps": list(tenant.get("apps", {}).get("enabled") or []),
        "frontend_host": str(frontend.get("host") or ""),
        "frontend_org": str(branding.get("organisationName") or ""),
        "frontend_theme": str(branding.get("themeClassname") or ""),
        "frontend_jumbotron": str(branding.get("jumbotronImageUrl") or ""),
        "frontend_favicon": str(branding.get("faviconUrl") or ""),
        "frontend_tls_issuer": str(tls.get("issuer") or DEFAULT_TLS_ISSUER),
        "frontend_tag": str(frontend.get("tag") or ""),
        "frontend_registry": str(frontend.get("registry") or ""),
        "frontend_repository": str(frontend.get("repository") or ""),
    }


def validate(fields):
    """Return a list of human-readable error strings ([] == valid).

    `fields` keys: name, environment, dbType, apps (list[str]); optional wave,
    frontend_tls_issuer, frontend_tag, frontend_theme.

    Mirrors validate-values.sh so a valid result here passes Nextcloud-base CI.
    Die spiegeling is een belofte die je moet onderhouden: op 2026-08-11 kreeg
    validate-values.sh checks op `tenant.frontend.*` die hier ontbraken, en
    daardoor kwamen twee kapotte tenant-bestanden ongehinderd op main terecht.
    Komt er een frontend-check bij aan de Nextcloud-base-kant, voeg hem hier ook
    toe — anders vangt de CI hem pas ná de merge."""
    errors = []
    name = (fields.get("name") or "").strip()
    env = (fields.get("environment") or "").strip()
    db = (fields.get("dbType") or "").strip()
    apps = fields.get("apps") or []

    m = _NAME_RE.match(name)
    if not m:
        errors.append("name must be '<org>-<accept|test|demo|prod>' (lowercase, "
                      "e.g. 'almere-accept')")
    else:
        suffix = m.group(2)
        expected_env = _SUFFIX_ENV[suffix]
        if env not in ENVS:
            errors.append(f"environment must be one of {ENVS}")
        elif env != expected_env:
            errors.append(f"environment must be '{expected_env}' for a '-{suffix}' "
                          f"tenant (got '{env}')")

    if db not in DB_TYPES:
        errors.append(f"dbType must be one of {DB_TYPES}")

    if not apps:
        errors.append("at least one app must be enabled")
    else:
        unknown = [a for a in apps if a not in KNOWN_APPS]
        if unknown:
            errors.append(f"unknown app(s): {', '.join(unknown)} "
                          f"(known: {', '.join(KNOWN_APPS)})")

    # Only meaningful for a custom host, but reject a bad value regardless: a
    # typo'd issuer silently becomes a cert-manager annotation nobody resolves.
    issuer = (fields.get("frontend_tls_issuer") or "").strip()
    if issuer and issuer not in TLS_ISSUERS:
        errors.append(f"frontend.tls.issuer must be one of {TLS_ISSUERS}")

    errors += _validate_app_versions(fields)
    errors += _validate_image(fields, "frontend_", "frontend",
                              tag_example="V1.0.260422-development")
    # De Nextcloud-image eist bovendien een versie in de tag — zie
    # _validate_image() voor waarom dat voor de frontend niet geldt.
    errors += _validate_image(fields, "nc_image_", "image",
                              tag_example="32.0.13-fpm", require_version=True)

    # Een verkeerd getypt thema geeft geen foutmelding maar een site zonder
    # huisstijl — de klasse bestaat simpelweg niet in de bundle.
    theme = (fields.get("frontend_theme") or "").strip()
    if theme and not _THEME_RE.match(theme):
        errors.append(
            f"frontend.branding.themeClassname '{theme}' moet de vorm "
            "'<naam>-theme' hebben (kleine letters, koppeltekens). Let op "
            "'-thema' in plaats van '-theme'")

    return errors


def _validate_app_versions(fields):
    """Valideer de per-app versie-pins. Returnt foutstrings.

    Spiegelt validate_app_versions_format() uit validate-values.sh, inclusief de
    aparte melding voor een leidende 'v' — dat is de fout die mensen maken omdat
    GitHub-releases `v0.7.12` heten terwijl het appstore-veld het zonder wil.

    Een pin op een app buiten PINNABLE_APPS kán niet via dit formulier ontstaan
    (de velden heten `version_<app>`), maar from_declaration() leest een
    hand-geschreven bestand terug en dan hoort een niet-gewirede naam als fout
    te verschijnen in plaats van stil te verdwijnen.
    """
    errors = []
    for app in PINNABLE_APPS:
        ver = (fields.get(f"version_{app}") or "").strip()
        if not ver:
            continue                      # geen pin: de app volgt de laatste release
        if ver.startswith("v"):
            errors.append(
                f"apps.versions.{app} mag niet met 'v' beginnen (kreeg '{ver}') — "
                f"gebruik '{ver[1:]}'")
        elif not _APP_VERSION_RE.match(ver):
            errors.append(
                f"apps.versions.{app} '{ver}' is geen geldige versie — drie "
                f"numerieke delen, eventueel met suffix (bijvoorbeeld '0.7.12' "
                f"of '0.2.10-unstable.4'). Niet '0.7', niet 'latest'")
    return errors


def _validate_image(fields, prefix, label, tag_example, require_version=False):
    """Valideer een registry/repository/tag-drieluik. Returnt foutstrings.

    Twee blokken gebruiken dit: `tenant.frontend.*` (de PWA-image) en het
    top-level `image:` (de Nextcloud-image). De vormregels zijn identiek — drie
    losse velden, nooit een volledige reference in één veld — dus staan ze hier
    één keer.

    `require_version` is het enige verschil, en alleen de Nextcloud-image zet
    het. Met `pullPolicy: IfNotPresent` hangt de draaiende versie bij een
    zwevende tag af van wanneer een node voor het laatst pullde: op 2026-08-19
    schoof `fpm-soap` van sha256:31123c8c naar sha256:80310a36 zonder dat er in
    git iets veranderde. De frontend-tags dragen geen semver en bestaande
    tenants leunen op die laksere regel, dus daar geldt het niet.
    """
    errors = []

    # Alleen het tag-deel. Een volledige reference hier levert een ongeldige
    # image op; zie de toelichting bij _TAG_RE.
    tag = (fields.get(f"{prefix}tag") or "").strip()
    if tag and not _TAG_RE.match(tag):
        if "/" in tag or ":" in tag:
            errors.append(
                f"{label}.tag '{tag}' bevat '/' of ':' — vul hier alleen het "
                f"tag-deel in (bijvoorbeeld '{tag_example}'), niet de "
                "volledige image-reference")
        else:
            errors.append(
                f"{label}.tag '{tag}' is geen geldige tag (letters, cijfers, "
                "'.', '_' en '-', beginnend met letter/cijfer/'_')")
    elif tag and require_version and image_version(tag) is None:
        errors.append(
            f"{label}.tag '{tag}' draagt geen versienummer — gebruik een "
            f"patch-tag zoals '{tag_example}', niet een zwevende tag. Met "
            "pullPolicy IfNotPresent hangt de draaiende versie anders af van "
            "wanneer een node voor het laatst pullde")

    repository = (fields.get(f"{prefix}repository") or "").strip()
    if repository:
        if ":" in repository:
            errors.append(
                f"{label}.repository '{repository}' bevat ':' — de tag hoort in "
                "het tag-veld")
        elif not _REPOSITORY_RE.match(repository):
            errors.append(
                f"{label}.repository '{repository}' is geen geldig pad "
                "(kleine letters, geen leidende of afsluitende '/', "
                "bijvoorbeeld 'conduction2022/woo-website-v2')")

    registry = (fields.get(f"{prefix}registry") or "").strip()
    if registry:
        if "/" in registry:
            errors.append(
                f"{label}.registry '{registry}' bevat '/' — vul hier alleen de "
                "host in (bijvoorbeeld 'docker.io'); het pad hoort in het "
                "repository-veld")
        elif not _REGISTRY_RE.match(registry):
            errors.append(
                f"{label}.registry '{registry}' is geen geldige host "
                "(bijvoorbeeld 'ghcr.io' of 'registry.local:5000')")
        # De ApplicationSet stelt de reference alleen samen als er een repository
        # is; een registry op zichzelf wordt stil genegeerd.
        if not repository:
            errors.append(
                f"{label}.registry is gezet zonder {label}.repository — vul "
                "beide in, of geen van beide")

    return errors


def image_version(tag):
    """De versie uit een image-tag als vergelijkbare tuple, of None.

    `32.0.6-fpm-soap` -> (32, 0, 6). `fpm-soap` en `latest` -> None: die dragen
    geen versie. Dat onderscheid is de hele reden dat deze functie bestaat —
    zie de toelichting bij validate() over zwevende tags.

    Alleen de drie leidende numerieke delen tellen. Het suffix (`-fpm`,
    `-fpm-soap`) zegt welke build het is, niet welke versie, en mag de
    vergelijking dus niet beinvloeden: 32.0.6-fpm en 32.0.6-fpm-soap zijn
    dezelfde Nextcloud-versie in een andere build.
    """
    match = _VERSION_RE.match(str(tag or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1, 2, 3))


def compare_versions(left, right):
    """-1 als `left` ouder is dan `right`, 0 bij gelijk, 1 als nieuwer.

    Beide argumenten zijn tags; None-versies geven None terug (onvergelijkbaar).

    Waarom niet op de string vergelijken: lexicaal is `"32.0.6-fpm-soap"` GROTER
    dan `"32.0.13-fpm"`, want '6' > '1'. Een stringvergelijking concludeert dus
    dat 32.0.6 nieuwer is dan 32.0.13 en laat de downgrade door — groen licht op
    exact het geval dat de controle moet vangen. Daarom parsen we de versie in
    plaats van de tekst te vergelijken, en daarom heeft dit eigen tests.
    """
    a, b = image_version(left), image_version(right)
    if a is None or b is None:
        return None
    return (a > b) - (a < b)


def _q(value):
    """Double-quote a scalar for YAML, escaping embedded quotes/backslashes."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(fields):
    """Render the tenant YAML as text. Assumes `validate(fields)` passed.

    Emits the minimal tenant block (name/environment/wave/dbType/apps) and an
    optional `frontend` block (host and/or branding) only when those fields are
    supplied — everything else is derived by the platform.

    Branding note. `frontend.branding` is read by react-base's `react-tenants`
    ApplicationSet, which turns each key into a `GATSBY_` env var on the
    frontend. `themeClassname` deserves care:

    - Blank is the safe value. The ApplicationSet falls back to
      `conduction-theme`, which ships with the bundled themes and renders out of
      the box. Deriving `<org>-theme` here would point at a theme that usually
      does not exist — exactly the bug react-base fixed on 2026-06-30, where
      onboarded tenants rendered without any theme.
    - Only set it when the tenant genuinely has its own bundled NL Design theme.
    - Creation is the moment that counts: the appset ignore-diffs the branding
      env (`^(GATSBY_|NL_DESIGN_)`) so devs can edit it live, which also means a
      value added to an *existing* tenant file does not reach a running
      frontend. A new tenant's frontend is created fresh, so what is declared
      here is what it starts with.

    TLS note. A `frontend.tls` block is emitted only for a host outside the
    platform domain; anything under `*.openwoo.app` is already covered by the
    shared wildcard, and overriding it would point the Ingress at a Secret
    nobody created. The Secret name is derived from the host, matching what the
    fleet already does — the certificate BYTES never travel through git or this
    portal, only the name does (see docs/custom-domain-cert.md)."""
    name = fields["name"].strip()
    env = fields["environment"].strip()
    wave = str(fields.get("wave") or "1").strip()
    db = fields["dbType"].strip()
    apps = list(fields["apps"])

    host = (fields.get("frontend_host") or "").strip()
    org = (fields.get("frontend_org") or "").strip()
    # Branding keys the react-tenants ApplicationSet turns into GATSBY_ env on the
    # frontend. Emitted only when supplied — see the branding note in render()'s
    # docstring for why a blank theme is the correct default.
    branding_extra = [
        ("themeClassname", (fields.get("frontend_theme") or "").strip()),
        ("jumbotronImageUrl", (fields.get("frontend_jumbotron") or "").strip()),
        ("faviconUrl", (fields.get("frontend_favicon") or "").strip()),
    ]
    extras = [(k, v) for k, v in branding_extra if v]

    lines = ["---", "tenant:", f"  name: {name}", f"  environment: {env}",
             f"  wave: {_q(wave)}", f"  dbType: {db}",
             # New-world tenants get ESO-managed secrets (generated in-cluster). The
             # flag gates charts/tenant-secret in the appset; existing tenants omit it.
             "  secrets:", "    managed: true",
             "  apps:", "    enabled:"]
    lines += [f"      - {a}" for a in apps]

    # Optionele versie-pins per app. Een key weglaten betekent "volg de laatste
    # release"; de ApplicationSet geeft dan `""` mee. Alleen de drie gewirede
    # apps kunnen hier staan — zie PINNABLE_APPS.
    pins = [(app, (fields.get(f"version_{app}") or "").strip()) for app in PINNABLE_APPS]
    pins = [(app, ver) for app, ver in pins if ver]
    if pins:
        lines.append("    versions:")
        lines += [f"      {app}: {_q(ver)}" for app, ver in pins]

    # Optionele image-pin voor de frontend. De ApplicationSet stelt de reference
    # samen als `<registry>/<repository>:<tag>` en levert dat als
    # pwa.image.image / pwa.image.tag. Drie losse velden, zodat een volledige
    # reference niet in één veld kan belanden — zie de toelichting bij _TAG_RE.
    # Zonder waarden volgt de frontend de platformstandaard uit common.yaml.
    tag = (fields.get("frontend_tag") or "").strip()
    registry = (fields.get("frontend_registry") or "").strip()
    repository = (fields.get("frontend_repository") or "").strip()

    if host or org or extras or tag or registry or repository:
        lines.append("  frontend:")
        if registry:
            lines.append(f"    registry: {_q(registry)}")
        if repository:
            lines.append(f"    repository: {_q(repository)}")
        if tag:
            lines.append(f"    tag: {_q(tag)}")
        if host:
            lines.append(f"    host: {host}")
        if is_custom_frontend_host(host):
            issuer = (fields.get("frontend_tls_issuer") or "").strip() or DEFAULT_TLS_ISSUER
            lines += ["    tls:",
                      f"      secretName: {tls_secret_name(host)}",
                      f"      issuer: {issuer}"]
        if org or extras:
            lines.append("    branding:")
            if org:
                lines.append(f"      organisationName: {_q(org)}")
            lines += [f"      {key}: {_q(value)}" for key, value in extras]

    # Optionele image-override voor Nextcloud zelf. BEWUST top-level en niet
    # onder `tenant:`: dit is een chart-value, geen hub-veld. Het werkt omdat het
    # tenantbestand als laatste in de `valueFiles` van de ApplicationSet staat en
    # dus van common.yaml wint.
    #
    # Geen `digest:`-veld, en dat is geen omissie: chart 8.9.0 rendert het niet,
    # dus de podspec zou alleen de tag dragen terwijl git een digest beweert. Wie
    # het toch nodig heeft, heeft een chart-wijziging nodig, geen formulierveld.
    nc_tag = (fields.get("nc_image_tag") or "").strip()
    nc_registry = (fields.get("nc_image_registry") or "").strip()
    nc_repository = (fields.get("nc_image_repository") or "").strip()

    if nc_tag or nc_registry or nc_repository:
        lines.append("")
        lines.append("image:")
        if nc_registry:
            lines.append(f"  registry: {_q(nc_registry)}")
        if nc_repository:
            lines.append(f"  repository: {_q(nc_repository)}")
        if nc_tag:
            lines.append(f"  tag: {_q(nc_tag)}")

    return "\n".join(lines) + "\n"
