#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: entrypoint
#
# webgui/server.py — hosted provisioning control-plane (Phase 1: core, no auth).
#
# A small Flask app that drives tenant provisioning from OUTSIDE the cluster,
# against a tenant's PUBLIC URL (a trusted domain — so it just works, unlike the
# internal service). It reuses scripts/provision.py via the tested
# provision_gui.build_command(): the form values become `provision.py all`,
# secrets are passed to the subprocess via env (never argv), and the step log is
# streamed back to the browser.
#
# Creds model A (chosen): the operator enters the tenant admin password + source
# API key in the form per run. The app stores NOTHING — no standing credentials.
#
# Auth (Phase 2): the app sits behind oauth2-proxy → Keycloak (which brokers
# Google). oauth2-proxy authenticates the operator and sets X-Forwarded-Email /
# X-Forwarded-User on the upstream request; the app reads that via current_user().
# When REQUIRE_AUTH is on (the default), every request except /healthz is refused
# (403) unless such a header is present — so a direct hit that bypasses the proxy
# fails closed. The proxy MUST be the only ingress (app bound to localhost / a
# NetworkPolicy); the header is trustworthy only because nothing else can reach
# the app. For local dev without a proxy, set REQUIRE_AUTH=false.
#
# Writes: read-only on the repo; the spawned provision.py mutates the *target
#   tenant* (the URL the operator enters). Secrets are never logged.
# Requires: python3.8+, Flask (webgui/requirements.txt), network egress to the
#   tenant's public URL.
#
# Usage:
#   pip install -r webgui/requirements.txt
#   REQUIRE_AUTH=false python3 webgui/server.py   # local dev, no proxy
#   python3 webgui/server.py                       # behind oauth2-proxy (default)
#   PORT=9000 python3 webgui/server.py
"""Hosted provisioning control-plane (Flask, Phase 2: behind oauth2-proxy)."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from urllib.parse import urlencode

from flask import Flask, Response, redirect, render_template, request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import provision_gui  # noqa: E402  — reuse the tested build_command()

# Tenant creation (Phase 3): render + validate a Nextcloud-base tenant file and
# open it as a PR. gitlib/tenants live alongside this module (webgui/).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitlib    # noqa: E402
import tenants   # noqa: E402
import argolib   # noqa: E402  — read-only Argo Application status (post-merge rollout check)
import assistant  # noqa: E402 — handboek-gegronde assistent (v1 strikt lezend)
import burnstore  # noqa: E402 — eenmalige reveal-tickets (geen secret at rest)
import certlib   # noqa: E402 — validatie + plaatsing van een klantcertificaat
import hashlib   # noqa: E402
import json      # noqa: E402
import re        # noqa: E402
import time      # noqa: E402 — venster voor de rate limit op /reveal
import datetime  # noqa: E402 — tijdstempel bij "al gedeeld"
# Parsing a declared tenant file needs a YAML reader. tenants.py stays
# dependency-free by design (it only *emits* text), so the parse lives here —
# PyYAML is already a webgui dependency for the handbook content layer.
import yaml      # noqa: E402

TENANTS_DIR = "nextcloud-platform/values/tenants"

# De platformbrede Nextcloud-image staat hier één keer; een tenant zonder eigen
# `image:`-blok draait deze versie. De image-downgrade-guard vergelijkt hiertegen
# wanneer het tenantbestand zelf geen override heeft. Env-instelbaar omdat het
# een pad in een ándere repo is.
COMMON_VALUES_PATH = os.environ.get("COMMON_VALUES_PATH",
                                    "nextcloud-platform/values/common.yaml")

# Nextcloud-base's governance-check weigert elke PR zonder het label dat bij de
# classificatie hoort; een PR die alleen tenantbestanden raakt classificeert als
# `tenant-additive`. Zonder label is élke portal-PR rood en moet een mens hem
# alsnog handmatig labelen — precies het handwerk dat het portaal wegneemt.
# Env-instelbaar, want de labelnaam is een afspraak in die andere repo.
TENANT_PR_LABEL = os.environ.get("TENANT_PR_LABEL", "change/tenant-additive")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Fail closed by default: refuse any request without an oauth2-proxy identity.
# Set REQUIRE_AUTH=false only for local dev where no proxy fronts the app.
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "true").strip().lower() not in (
    "false", "0", "no", "off")


def _label_pr(number, label=None):
    """Put the governance label on a freshly opened PR.

    Best-effort on purpose. By the time this runs the PR exists, so a failing
    label call must not turn a successful request into an error: a 500 would
    tell the operator nothing happened while an unlabelled PR sits open. Log it
    and let the caller return the PR.
    """
    name = label or TENANT_PR_LABEL
    if not number or not name:
        return
    try:
        gitlib.add_labels(number, [name])
    except gitlib.GitlibError as exc:
        app.logger.warning("PR label failed (PR stays open, label by hand): "
                           "pr=%s label=%s status=%s detail=%s",
                           number, name, exc.status, exc.detail)


def current_user():
    """Operator identity, set by oauth2-proxy (Keycloak/Google). Falls back to
    '-' when no proxy header is present (i.e. unauthenticated)."""
    return (request.headers.get("X-Forwarded-Email")
            or request.headers.get("X-Forwarded-User")
            or "-")


@app.before_request
def _require_operator():
    """Defence in depth: with REQUIRE_AUTH on, every route except the health
    probe needs an authenticated operator. The header is only trustworthy
    because oauth2-proxy is the sole ingress — see the module docstring.

    Ook `/reveal/<token>` valt hieronder. Besluit 2026-08-07: een adminwachtwoord
    wordt alleen aan Conduction-medewerkers getoond. De oorspronkelijke opzet
    (een product owner zonder account, met het token als enige poort) is
    daarmee vervallen — het token blijft eenmalig en kortlevend, maar is nu een
    tweede slot achter de login in plaats van het enige. oauth2-proxy heeft
    bewust geen skip_auth_routes, dus beide poorten zeggen hetzelfde."""
    if request.path == "/healthz":
        return None
    if REQUIRE_AUTH and current_user() == "-":
        return Response("forbidden: no authenticated operator — this app must be "
                        "reached via oauth2-proxy\n",
                        status=403, mimetype="text/plain")
    return None


@app.get("/")
def index():
    # Landing page: use-case cards (create tenant / provision config) + logout.
    return render_template("home.html", user=current_user())


@app.get("/provision-config")
def provision_config_form():
    # The original config-provisioning form (POSTs to /provision, unchanged).
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return "ok\n", 200, {"Content-Type": "text/plain"}


# Full logout: clear the oauth2-proxy session AND end the Keycloak SSO session
# (RP-initiated logout). Without the Keycloak hop, skip_provider_button silently
# re-logs-in on the next request. The Keycloak end-session URL + post-logout
# redirect are configurable; defaults match this deployment.
KEYCLOAK_LOGOUT_URL = os.environ.get(
    "KEYCLOAK_LOGOUT_URL",
    "https://iam.commonground.nu/realms/commonground/protocol/openid-connect/logout")
POST_LOGOUT_REDIRECT = os.environ.get("POST_LOGOUT_REDIRECT", "https://platform.commonground.nu/")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "openwoo-provisioner")


@app.get("/logout")
def logout():
    kc = KEYCLOAK_LOGOUT_URL + "?" + urlencode(
        {"post_logout_redirect_uri": POST_LOGOUT_REDIRECT, "client_id": OIDC_CLIENT_ID})
    return redirect("/oauth2/sign_out?" + urlencode({"rd": kc}))


@app.post("/provision")
def provision():
    form = request.form
    values = {k: form.get(k, "") for k in
              ("base", "user", "password", "source_url", "api_interface_id", "apikey", "job_user")}
    values["force_import"] = bool(form.get("force_import"))
    values["run_syncs"] = bool(form.get("run_syncs"))
    values["dry_run"] = bool(form.get("dry_run"))
    values["in_cluster"] = bool(form.get("in_cluster"))
    try:
        argv, env = provision_gui.build_command(values)
    except ValueError as exc:
        return Response(f"error: {exc}\n", status=400, mimetype="text/plain")

    user = current_user()
    # Audit: who + what + options. NEVER the password/apikey (they live only in env).
    # Log the PUBLIC base (recognisable in audit), plus whether in-cluster mode
    # rewrote the connect target. Never the internal svc URL — keeps logs uniform.
    app.logger.info("provision requested: user=%s base=%s run_syncs=%s force_import=%s in_cluster=%s",
                    user, values["base"], values["run_syncs"], values["force_import"],
                    values["in_cluster"])

    def stream():
        yield f"# provisioning {values['base']} (requested by {user})\n\n"
        proc = subprocess.Popen(argv, env=env, cwd=str(REPO_ROOT),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            yield line
        proc.wait()
        yield f"\n--- exit code {proc.returncode} ---\n"
        app.logger.info("provision finished: user=%s base=%s exit=%s",
                        user, values["base"], proc.returncode)

    return Response(stream(), mimetype="text/plain")


@app.get("/tenant")
def tenant_form():
    """Aanmaken. Bewerken zit op /tenant/<naam>/edit — één scherm, één taak:
    'Nieuwe WOO-omgeving' die ook bestaande omgevingen bewerkte was verwarrend."""
    return render_template("tenant.html")


@app.get("/branding")
def branding_picker():
    """Kies eerst een omgeving; het brandingscherm zelf zit per tenant."""
    return render_template("branding.html")


@app.get("/tenant/<name>/edit")
def tenant_edit_form(name):
    """Branding van een bestaande omgeving: wat de frontend toont, plus adres en
    certificaat. Weigert wat de portal niet beheert."""
    if not _TENANT_RE.fullmatch(name):
        return Response("ongeldige naam\n", status=400, mimetype="text/plain")
    try:
        declared = _declaration(name)
    except gitlib.GitlibError as exc:
        return Response(f"kan de omgeving niet ophalen: {exc.detail}\n",
                        status=502, mimetype="text/plain")
    return render_template("edit.html", tenant=name, declared=declared)


@app.post("/tenant")
def tenant_create():
    """Aanmaken. Bestaat de omgeving al, dan verwijst dit naar de bewerkpagina."""
    return _tenant_write(request.form, is_edit=False)


@app.post("/tenant/<name>/edit")
def tenant_update(name):
    """Bewerken van een bestaande omgeving. De naam komt uit de URL, niet uit het
    formulier — zo kan een bewerkscherm nooit per ongeluk iets anders raken."""
    if not _TENANT_RE.fullmatch(name):
        return {"errors": ["invalid tenant name"]}, 400
    org, _, env_suffix = name.rpartition("-")
    form = request.form.copy()
    form["org"], form["environment"] = org, tenants.env_for_suffix(env_suffix)
    return _tenant_write(form, is_edit=True)


def _tenant_write(form, is_edit):
    """Validate the form, render tenant-<name>.yaml, and open a PR on the tenants
    repo as the token's identity. The operator (oauth2-proxy) is stamped as
    requested-by; the merge stays a human gate. Returns JSON {pr_url, pr_number}.

    The portal NEVER creates secrets or touches the cluster — tenant secrets are
    generated in-cluster (ESO). Its only privileged action is opening this PR."""
    _is_edit = is_edit
    # Minimal operator input: bare org + environment. Everything else is derived
    # (name=<org>-<env>, all 3 apps, branding 'Gemeente <Org>', db=postgres,
    # host blank => platform derives the hostname). Advanced overrides optional.
    org = form.get("org", "")
    environment = form.get("environment", "")
    errors = tenants.validate_org(org, environment)
    if errors:
        return {"errors": errors}, 400
    fields = tenants.from_org(
        org, environment,
        dbType=form.get("dbType", ""),
        display=form.get("frontend_org", ""),
        host=form.get("frontend_host", ""),
        theme=form.get("frontend_theme", ""),
        jumbotron=form.get("frontend_jumbotron", ""),
        favicon=form.get("frontend_favicon", ""),
        tls_issuer=form.get("frontend_tls_issuer", ""),
        tag=form.get("frontend_tag", ""),
        registry=form.get("frontend_registry", ""),
        repository=form.get("frontend_repository", ""),
        nc_tag=form.get("nc_image_tag", ""),
        nc_registry=form.get("nc_image_registry", ""),
        nc_repository=form.get("nc_image_repository", ""),
        versions={app: form.get(f"version_{app}", "")
                  for app in tenants.PINNABLE_APPS},
    )
    # defense-in-depth: the derived fields must still pass the full validator
    errors = tenants.validate(fields)
    if errors:
        return {"errors": errors}, 400

    user = current_user()
    name = fields["name"].strip()
    path = f"{TENANTS_DIR}/{tenants.filename(name)}"
    content = tenants.render(fields)

    # Does it already exist? That decides create-vs-update, and it is also the
    # guard: the portal may only rewrite a file it could have written itself.
    try:
        declared = _declaration(name)
    except gitlib.GitlibError as exc:
        # Cannot tell create from update: refuse rather than guess. Guessing
        # "create" against an existing file fails on the API anyway, with a far
        # less helpful message than this one.
        app.logger.warning("tenant declaration lookup failed: name=%s detail=%s",
                           name, exc.detail)
        return {"errors": [f"kan tenant-status niet ophalen: {exc.detail}"]}, 502
    if declared["exists"] and not declared["editable"]:
        return {"errors": [
            f"tenant-{name}.yaml is met de hand aangepast en wordt niet door de "
            f"portal beheerd: {', '.join(declared['unknown'])}. Wijzig het bestand "
            f"rechtstreeks in Nextcloud-base."]}, 409
    # Aanmaken en bewerken zijn nu gescheiden schermen. Deze route maakt aan;
    # bestaat de omgeving al, dan hoort de bewerkpagina erbij en zegt dat.
    if declared["exists"] and not _is_edit:
        return {"errors": [f"{name} bestaat al — pas hem aan via /tenant/{name}/edit"],
                "edit_url": f"/tenant/{name}/edit"}, 409

    # De image-downgrade-guard staat hier en niet direct na validate(): laag 1
    # heeft `declared` nodig om de effectieve huidige tag te bepalen, en laag 3
    # om create van update te scheiden. Wel nog vóór de PR — een geblokkeerde
    # downgrade mag geen branch achterlaten.
    image_errors, image_warnings = _image_guard(name, fields, declared)
    if image_errors:
        app.logger.warning("image guard blocked: user=%s name=%s errors=%s",
                           user, name, image_errors)
        return {"errors": image_errors, "warnings": image_warnings}, 400

    updating = declared["exists"]
    verb, branch = ("update", f"edit-tenant/{name}") if updating else ("add", f"add-tenant/{name}")
    commit_msg = (f"{verb} tenant: {name}\n\n"
                  f"Opened from the OpenWoo provisioning portal.\n"
                  f"requested-by: {user}\n")
    pr_body = (f"{'Updates' if updating else 'Adds'} tenant `{name}` via the OpenWoo "
               f"provisioning portal.\n\n"
               f"- requested-by: `{user}`\n"
               f"- machine-authored: review before merge.\n")
    if fields.get("nc_image_tag"):
        # De reviewer moet zien dat dit een afwijkende Nextcloud-build is en
        # waartegen de guard heeft vergeleken. PR #100 (Nextcloud-base,
        # 2026-08-26) had een image-override zonder één woord daarover in de
        # body, met een onderbouwing die naar een tenant verwees die niet
        # bestond.
        pr_body += (f"- Afwijkende Nextcloud-image: "
                    f"`{fields.get('nc_image_registry') or '<default>'}/"
                    f"{fields.get('nc_image_repository') or '<default>'}:"
                    f"{fields['nc_image_tag']}`. Gecontroleerd op downgrade "
                    f"tegen git en Argo.\n")
    for warning in image_warnings:
        pr_body += f"- ⚠️ {warning}\n"
    if updating:
        # Branding env is ignore-diffed on the frontend Deployment, so a change
        # here does NOT reach a running frontend. Saying so in the PR beats
        # someone discovering it after the merge.
        pr_body += ("- ⚠️ Branding (`themeClassname`, `organisationName`, jumbotron, "
                    "favicon) wordt op een **draaiende** frontend niet toegepast: de "
                    "ApplicationSet ignore-difft die env. `frontend.tls`, `host` en "
                    "`apps` gelden wél direct.\n")

    app.logger.info("tenant PR requested: user=%s name=%s env=%s db=%s update=%s",
                    user, name, fields["environment"], fields["dbType"], updating)
    try:
        propose = gitlib.propose_update if updating else gitlib.propose_file
        result = propose(
            branch=branch, path=path, content=content,
            commit_message=commit_msg,
            pr_title=f"{verb} tenant: {name}", pr_body=pr_body)
    except gitlib.GitlibError as exc:
        # 409 = branch/file already exists (tenant in flight); 0 = misconfig/unreachable.
        status = 409 if exc.status == 409 else (502 if exc.status in (0, 500, 502, 503) else 400)
        app.logger.warning("tenant PR failed: user=%s name=%s status=%s detail=%s",
                            user, name, exc.status, exc.detail)
        return {"errors": [exc.detail]}, status

    _label_pr(result.get("number"))
    app.logger.info("tenant PR opened: user=%s name=%s pr=%s update=%s",
                    user, name, result.get("number"), updating)
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number"),
            "tenant": name, "updated": updating,
            # Niet-blokkerend, maar de operator moet het zien: het staat ook in
            # de PR-body, en die leest hij pas na het klikken.
            "warnings": image_warnings}, 201


def _common_image_tag():
    """De platformstandaard-tag uit `values/common.yaml`, of None.

    Dat is de versie die een tenant draait zolang hij geen eigen `image:`-blok
    heeft, en dus de referentie waartegen een override wordt vergeleken.
    """
    raw, _sha = gitlib.get_file(COMMON_VALUES_PATH)
    doc = yaml.safe_load(raw) or {}
    tag = ((doc.get("image") or {}).get("tag"))
    return str(tag).strip() if tag else None


def _image_guard(name, fields, declared):
    """De image-downgrade-guard. Returnt (errors, warnings).

    Handhaaft de regel uit Nextcloud-base docs/ADDING-TENANT.md die een
    formulier niet met veldvalidatie kan uitdrukken: **nooit een lagere versie
    dan wat de tenant draait**. `/var/www/html` is een PVC, de upstream-
    entrypoint stopt met exit 1 op een ouder image, en met `selfHeal: true`
    blijft Argo het proberen — herstel gaat via het tenantbestand, niet via
    kubectl.

    Drie lagen, met opzet verschillende hardheid:

    1. git      — de effectieve huidige tag (tenantbestand, anders common.yaml).
                  Blokkeert. Dit is de bron van waarheid voor wat er hoort te
                  draaien.
    2. argo     — `status.summary.images` van `nc-<naam>`. Blokkeert. Wijkt het
                  af van laag 1, dan is dat een waarschuwing en wint git: een
                  gedrifte cluster mag een correcte wijziging niet vetoen.
    3. historie — bestond het tenantbestand eerder en is het verwijderd?
                  Waarschuwt alleen. Of het volume er nog staat weet het portaal
                  niet (het mag geen namespaces lezen), dus blokkeren op een
                  misschien zou elke re-add onmogelijk maken.

    Een verse tenant zonder historie mag elke versie: er is geen volume om
    tegenaan te lopen. Dat onderscheid is de reden dat de lagen naar `declared`
    en de historie kijken en niet alleen naar de tags.
    """
    errors, warnings = [], []
    proposed = (fields.get("nc_image_tag") or "").strip()
    exists = bool(declared.get("exists"))

    # --- laag 3: is dit een re-add van een verwijderd bestand? -------------
    previous = None
    if not exists:
        try:
            history = gitlib.file_history(f"{TENANTS_DIR}/{tenants.filename(name)}", limit=1)
        except gitlib.GitlibError as exc:
            # Deze laag waarschuwt en blokkeert nooit, dus mag zijn eigen
            # falen ook niet blokkeren — anders houdt een onbereikbare
            # historie-endpoint het aanmaken van élke tenant tegen. Dat de
            # check niet gelukt is, moet wel zichtbaar zijn: een stille
            # overslag leest als "geen historie".
            history = []
            warnings.append(
                f"kon de git-historie van tenant-{name}.yaml niet lezen "
                f"({exc.detail}) — niet vastgesteld of deze tenant eerder heeft "
                f"bestaan. Bestaat de namespace nog, dan gelden de versie- en "
                f"databaseregels van een bestaande tenant.")
        if history:
            previous = history[0]
            warnings.append(
                f"tenant-{name}.yaml heeft eerder bestaan en is verwijderd "
                f"({previous.get('date') or 'datum onbekend'}: "
                f"{previous.get('message') or 'geen bericht'}). Beide "
                f"ApplicationSets zetten preserveResourcesOnDeletion, dus de "
                f"namespace en het PVC kunnen er nog staan. Controleer dat met "
                f"`kubectl get ns {name}` voordat dit gemerged wordt: op een "
                f"bestaand volume gelden de versie- en databaseregels van een "
                f"BESTAANDE tenant, niet die van een nieuwe.")

    if not proposed:
        return errors, warnings          # geen override: niets te vergelijken

    # --- laag 1: git -------------------------------------------------------
    current = (declared.get("fields") or {}).get("nc_image_tag") if exists else None
    if exists and not current:
        try:
            current = _common_image_tag()
        except (gitlib.GitlibError, yaml.YAMLError) as exc:
            return (errors + [f"kan {COMMON_VALUES_PATH} niet lezen ({exc}) — "
                              f"zonder de platformstandaard is niet vast te "
                              f"stellen of dit een downgrade is"], warnings)

    if exists and current and tenants.compare_versions(proposed, current) == -1:
        errors.append(
            f"image.tag '{proposed}' is ouder dan de versie die {name} nu draait "
            f"('{current}'). Downgraden werkt niet: /var/www/html is een PVC, de "
            f"entrypoint stopt met exit 1 en Argo blijft met selfHeal opnieuw "
            f"proberen. Bouw de variant eerst op {current} of hoger.")

    # --- laag 2: argo ------------------------------------------------------
    repository = (fields.get("nc_image_repository") or "").strip()
    if exists and repository:
        try:
            status = argolib.app_status(f"nc-{name}")
        except argolib.ArgoError as exc:
            # Argo is de kruiscontrole, niet de bron van waarheid. Onbereikbaar
            # betekent geen tweede mening — melden, niet weigeren, want laag 1
            # heeft de blokkerende uitspraak al gedaan.
            warnings.append(f"Argo-status van nc-{name} niet op te halen "
                            f"({exc.detail}); alleen tegen git vergeleken.")
        else:
            live = argolib.image_for_repository(status.get("images"), repository)
            live_tag = str(live).rsplit(":", 1)[-1] if live and ":" in str(live) else None
            if live_tag:
                if tenants.compare_versions(proposed, live_tag) == -1:
                    errors.append(
                        f"image.tag '{proposed}' is ouder dan de image die Argo "
                        f"op nc-{name} ziet draaien ('{live_tag}').")
                if current and tenants.compare_versions(live_tag, current) != 0:
                    warnings.append(
                        f"git zegt '{current}' en Argo ziet '{live_tag}' op "
                        f"nc-{name} — die lopen uiteen. Git is hier "
                        f"maatgevend; controleer de drift apart.")

    return errors, warnings


def _declaration(name):
    """What the tenants repo says about `name` right now.

    Returns {exists, editable, unknown, fields}. `editable` is False when the
    file carries anything render() would not emit — re-rendering would drop it,
    so the portal shows it instead of touching it.
    """
    out = {"exists": False, "editable": False, "unknown": [], "fields": None}
    try:
        raw, _sha = gitlib.get_file(f"{TENANTS_DIR}/{tenants.filename(name)}")
    except gitlib.GitlibError as exc:
        if exc.status == 404:
            return out                                   # not declared yet
        raise
    out["exists"] = True
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        out["unknown"] = [f"<onleesbare YAML: {str(exc)[:80]}>"]
        return out
    out["unknown"] = tenants.unknown_keys(doc)
    out["editable"] = not out["unknown"]
    out["fields"] = tenants.from_declaration(doc)
    return out


@app.get("/tenant/<name>/declaration")
def tenant_declaration(name):
    """Current declared values for `name`, so the form can show what is there
    instead of pretending every tenant is new. Read-only."""
    if not _TENANT_RE.fullmatch(name):
        return {"errors": ["invalid tenant name"]}, 400
    try:
        return _declaration(name), 200
    except gitlib.GitlibError as exc:
        return {"errors": [exc.detail]}, 502


@app.get("/tenant/batch")
def tenant_batch_form():
    return render_template("batch.html")


@app.post("/tenant/batch")
def tenant_batch_create():
    """Batch: one org per line + environment -> ONE PR adding all tenant files."""
    form = request.form
    environment = form.get("environment", "")
    orgs = [o.strip() for o in form.get("orgs", "").splitlines() if o.strip()]
    if not orgs:
        return {"errors": ["enter at least one organisation (one per line)"]}, 400

    errors = []
    for o in orgs:
        errors += [f"{o}: {e}" for e in tenants.validate_org(o, environment)]
    if len(set(orgs)) != len(orgs):
        errors.append("duplicate organisation in the list")
    if errors:
        return {"errors": errors}, 400

    user = current_user()
    files, names = [], []
    for o in orgs:
        fields = tenants.from_org(o, environment)
        names.append(fields["name"])
        files.append((f"{TENANTS_DIR}/{tenants.filename(fields['name'])}", tenants.render(fields)))

    branch = "add-tenants/" + hashlib.sha1(",".join(sorted(names)).encode()).hexdigest()[:10]
    commit_msg = f"add {len(names)} tenants ({environment})\n\nrequested-by: {user}\n"
    pr_body = ("Adds tenants via the OpenWoo portal (batch):\n"
               + "\n".join(f"- `{n}`" for n in names)
               + f"\n\nrequested-by: `{user}` — machine-authored, review before merge.\n")
    app.logger.info("batch PR requested: user=%s n=%d env=%s", user, len(names), environment)
    try:
        result = gitlib.propose_files(branch=branch, files=files, commit_message=commit_msg,
                                      pr_title=f"add {len(names)} tenants ({environment})", pr_body=pr_body)
    except gitlib.GitlibError as exc:
        status = 409 if exc.status == 409 else (502 if exc.status in (0, 500, 502, 503) else 400)
        return {"errors": [exc.detail]}, status
    _label_pr(result.get("number"))
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number"),
            "count": len(names), "tenants": names}, 201


_TENANT_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?")

# Gate for the reveal flow. Off by default: the route reads a tenant Secret, so
# it stays dark until an operator deliberately turns it on for this deployment.
REVEAL_ENABLED = os.environ.get("REVEAL_ENABLED", "false").strip().lower() in (
    "true", "1", "yes", "on")


@app.post("/tenant/<name>/secret-link")
def secret_link(name):
    """Mint a single-use link that shows `name`'s initial admin password once.

    Operator-gated like every other mutating route. Returns the URL; the
    operator passes it to the product owner over whatever channel they already
    use. The password itself never passes through this response, and is never
    logged — only the fact that a link was minted, by whom, for which tenant."""
    if not REVEAL_ENABLED:
        return {"errors": ["reveal flow disabled (set REVEAL_ENABLED=true)"]}, 404
    if not _TENANT_RE.fullmatch(name):
        return {"errors": ["invalid tenant name"]}, 400
    user = current_user()
    try:
        # Fail before minting if there is nothing to reveal, so the operator
        # learns now instead of the product owner learning at the link.
        if burnstore.read_admin_password(name) is None:
            return {"errors": [f"no nextcloud-password in secret 'nextcloud-secrets' "
                               f"for namespace '{name}'"]}, 404
        token = burnstore.mint(name, user)
    except burnstore.AlreadyMintedError as exc:
        # Eén overdracht per omgeving. Zeg wie en wanneer — dat is bruikbaarder
        # dan een kale weigering, en het is precies wat een audit wil zien.
        when = exc.record.get("minted_at")
        stamp = (datetime.datetime.fromtimestamp(when, datetime.timezone.utc)
                 .strftime("%Y-%m-%d %H:%M UTC")) if when else "eerder"
        app.logger.info("secret-link refused (already minted): user=%s tenant=%s", user, name)
        return {"errors": [f"voor {name} is al een wachtwoordlink gemaakt "
                           f"({stamp}, door {exc.record.get('requested_by', 'onbekend')}). "
                           f"Dat gebeurt één keer per omgeving."],
                "already_minted": True}, 409
    except burnstore.BurnstoreError as exc:
        app.logger.warning("secret-link failed: user=%s tenant=%s err=%s", user, name, exc)
        return {"errors": [str(exc)]}, 502
    app.logger.info("secret-link minted: user=%s tenant=%s ttl=%ss",
                    user, name, burnstore.TTL_SECONDS)
    return {"reveal_url": f"{request.host_url.rstrip('/')}/reveal/{token}",
            "tenant": name, "ttl_seconds": burnstore.TTL_SECONDS}, 201


# Rate limit op de reveal-route. design.md beloofde dit; het stond er niet.
# Met een token van 256 bits is brute force geen realistisch pad — dit is de
# formaliteit die voorkomt dat iemand het pad überhaupt probeert, en dat een
# scanner de log volschrijft. Env-tunable, zoals elke limiet hier.
REVEAL_RATE_MAX = int(os.environ.get("REVEAL_RATE_MAX", "20"))
REVEAL_RATE_WINDOW = int(os.environ.get("REVEAL_RATE_WINDOW", "300"))
_reveal_hits = {}


def _rate_limited(ip, now=None):
    """True als dit IP zijn budget in het venster op heeft.

    Bewust in-memory: bij één replica is dat genoeg, en een gedeelde teller zou
    een datastore introduceren voor een limiet die alleen scanners raakt.
    """
    now = time.time() if now is None else now
    hits = [t for t in _reveal_hits.get(ip, []) if now - t < REVEAL_RATE_WINDOW]
    hits.append(now)
    _reveal_hits[ip] = hits
    if len(_reveal_hits) > 1000:          # geen onbegrensde groei door spoofing
        for k in [k for k, v in _reveal_hits.items() if not v or now - v[-1] > REVEAL_RATE_WINDOW]:
            _reveal_hits.pop(k, None)
    return len(hits) > REVEAL_RATE_MAX


@app.get("/reveal/<token>")
def reveal(token):
    """Show a tenant's initial admin password exactly once. NOT operator-gated.

    The token is the credential (see _require_operator). The ticket is burned
    before the password is fetched, so a failure here cannot be retried into a
    second read. Expired and already-used are the same 404 on purpose: a probe
    must not be able to tell them apart."""
    if not REVEAL_ENABLED:
        return Response("Deze link werkt niet.\n", status=404, mimetype="text/plain")
    # Deze route staat als enige buiten de proxy-login (skip_auth_routes), dus
    # de rem zit hier. Nooit het token loggen — dat is de credential.
    client_ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                 or request.remote_addr or "-")
    if _rate_limited(client_ip):
        app.logger.warning("reveal rate-limited: ip=%s", client_ip)
        return Response("Te veel pogingen. Probeer het later opnieuw.\n",
                        status=429, mimetype="text/plain")
    try:
        entry = burnstore.claim(token)
    except burnstore.BurnstoreError:
        # Never leak store internals to an unauthenticated caller.
        app.logger.warning("reveal failed: store unavailable")
        return Response("Deze link werkt nu niet. Probeer het later opnieuw.\n",
                        status=503, mimetype="text/plain")
    if entry is None:
        return Response("Deze link is niet (meer) geldig. Hij werkt eenmalig en "
                        "verloopt vanzelf.\n", status=404, mimetype="text/plain")

    tenant = entry.get("tenant", "")
    try:
        password = burnstore.read_admin_password(tenant)
    except burnstore.BurnstoreError:
        password = None
    # Audit WHAT happened, never the value.
    app.logger.info("reveal claimed: tenant=%s minted_by=%s found=%s",
                    tenant, entry.get("requested_by", "-"), password is not None)
    if password is None:
        return Response("Het wachtwoord is niet meer op te halen. Vraag om een "
                        "nieuwe link.\n", status=404, mimetype="text/plain")
    return render_template("reveal.html", tenant=tenant, password=password), 200


@app.post("/tenant/<name>/certificate")
def tenant_certificate(name):
    """Neem een door de klant geleverd TLS-paar aan, valideer het en schrijf het
    als Secret in de namespace van de tenant.

    Dit is `certswap` in het portaal. De handeling is dezelfde, de blootstelling
    niet: sleutelmateriaal gaat nu door dit proces. Daarom valideren we vóór het
    schrijven, loggen we nooit de inhoud, en is de secretnaam AFGELEID van de
    host — nooit overgenomen uit het verzoek, zodat een upload geen ander
    secret kan overschrijven."""
    if not _TENANT_RE.fullmatch(name):
        return {"errors": ["invalid tenant name"]}, 400
    host = (request.form.get("host") or "").strip().lower()
    if not tenants.is_custom_frontend_host(host):
        return {"errors": ["geef de eigen frontend-host op; op het platformdomein "
                           "dekt het wildcard-certificaat het al"]}, 400

    cert_file, key_file = request.files.get("cert"), request.files.get("key")
    if not cert_file or not key_file:
        return {"errors": ["lever zowel het certificaat als de sleutel aan"]}, 400
    cert_pem, key_pem = cert_file.read(), key_file.read()

    user = current_user()
    try:
        summary = certlib.validate(cert_pem, key_pem, host)
        secret_name = tenants.tls_secret_name(host)
        action = certlib.write_secret(name, secret_name, cert_pem, key_pem)
    except certlib.CertError as exc:
        # De melding beschrijft wat er mis is, nooit wat erin stond.
        app.logger.warning("certificate rejected: user=%s tenant=%s reason=%s",
                           user, name, exc)
        return {"errors": [str(exc)]}, 400
    finally:
        del cert_pem, key_pem

    app.logger.info("certificate %s: user=%s tenant=%s secret=%s expires=%s",
                    action, user, name, secret_name, summary["not_after"])
    return {"secret": secret_name, "action": action, **summary}, 201


@app.get("/tenant/delete")
def tenant_delete_form():
    return render_template("delete.html", tenant=request.args.get("tenant", ""))


@app.post("/tenant/delete")
def tenant_delete():
    """Open a PR that REMOVES a tenant file. On merge the ApplicationSet drops
    the Applications, but `preserveResourcesOnDeletion: true` keeps everything
    they rolled out — the namespace keeps running, frontend included. The PR
    body says so and names the cleanup tool (destructive, human-reviewed)."""
    tenant = request.form.get("tenant", "").strip()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", tenant):
        return {"errors": ["invalid tenant name"]}, 400
    user = current_user()
    path = f"{TENANTS_DIR}/{tenants.filename(tenant)}"
    branch = f"delete-tenant/{tenant}"
    commit_msg = f"remove tenant: {tenant}\n\nrequested-by: {user}\n"
    pr_body = (f"Removes tenant `{tenant}` via the OpenWoo portal.\n\n"
               f"- requested-by: `{user}`\n"
               f"- ⚠️ Merging this does **not** take `{tenant}` off the air. The "
               f"ApplicationSet removes the Applications (`nc-{tenant}`, "
               f"`{tenant}-reactfront`), but `preserveResourcesOnDeletion: true` "
               f"keeps the **resources**: the namespace, its PVCs and secrets, and "
               f"the frontend Deployment with its Ingress — which keeps serving "
               f"traffic until someone removes it.\n"
               f"- Cleanup tool: `scripts/cleanup-tenant.sh --tenant {tenant}` "
               f"(openwoo-app-config) — plan first, then `--execute`. Run it only "
               f"**after** this PR is merged. DNS needs no action: external-dns "
               f"drops the Cloudflare record once the Ingress is gone.\n")
    app.logger.info("delete PR requested: user=%s tenant=%s", user, tenant)
    try:
        result = gitlib.propose_deletion(branch=branch, path=path, commit_message=commit_msg,
                                         pr_title=f"remove tenant: {tenant}", pr_body=pr_body)
    except gitlib.GitlibError as exc:
        status = (404 if exc.status == 404 else 409 if exc.status == 409
                  else 502 if exc.status in (0, 500, 502, 503) else 400)
        return {"errors": [exc.detail]}, status
    _label_pr(result.get("number"))
    app.logger.info("delete PR opened: user=%s tenant=%s pr=%s",
                    user, tenant, result.get("number"))
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number"), "tenant": tenant}, 201


@app.get("/tenant/pr-status")
def tenant_pr_status():
    """Poll a PR's state so the form can show open -> merged and then hand off to
    the provisioning use case once it's merged."""
    number = request.args.get("number", "")
    if not number.isdigit():
        return {"errors": ["invalid PR number"]}, 400
    try:
        return gitlib.get_pr(number), 200
    except gitlib.GitlibError as exc:
        return {"errors": [exc.detail]}, 502


@app.get("/tenant/argo-status")
def tenant_argo_status():
    """After merge: poll the Argo Application nc-<tenant> sync/health so the form
    can show a green check before handing off to provisioning.

    Carries `reactfront` (the same {exists, sync, health} shape) for
    `<tenant>-reactfront`. The delete path needs it: that app has its own
    ApplicationSet and its own name, so "nc-<tenant> is gone" says nothing about
    whether the frontend still exists. Read-only, same RBAC as the rest
    (applications: get,list,watch).
    """
    tenant = request.args.get("tenant", "")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", tenant):
        return {"errors": ["invalid tenant name"]}, 400
    try:
        status = argolib.app_status(f"nc-{tenant}")
        status["reactfront"] = argolib.app_status(f"{tenant}-reactfront")
        return status, 200
    except argolib.ArgoError as exc:
        return {"errors": [exc.detail]}, 502


@app.get("/dashboard.json")
def dashboard_data():
    """Landing-page overview: tenant Argo apps (nc-*) + recent tenant PRs. Each
    source fails independently (partial errors reported) so the page still loads."""
    # reveal_enabled stuurt de UI: zonder vlag heeft een knop die 404 geeft
    # geen zin, en met vlag moet hij vindbaar zijn zonder curl.
    # `minted` zegt welke omgevingen hun wachtwoordlink al gehad hebben; die
    # krijgen geen knop meer, want dat gebeurt één keer per omgeving.
    out = {"tenants": [], "prs": [], "errors": [],
           "reveal_enabled": REVEAL_ENABLED, "minted": []}
    if REVEAL_ENABLED:
        try:
            out["minted"] = burnstore.minted_tenants()
        except burnstore.BurnstoreError as exc:
            out["errors"].append(f"reveal-status: {exc}")
    try:
        out["tenants"] = argolib.list_apps()
    except argolib.ArgoError as exc:
        out["errors"].append(f"argo: {exc.detail}")
    try:
        out["prs"] = gitlib.list_prs()
    except gitlib.GitlibError as exc:
        out["errors"].append(f"git: {exc.detail}")
    return out, 200


@app.get("/assistant")
def assistant_page():
    """Chatvenster: vragen over het platform, antwoorden gegrond in het
    handboek met herkomst (change add-platform-assistant, v1 strikt lezend)."""
    return render_template("assistant.html", user=current_user())


@app.post("/api/assistant/ask")
def assistant_ask():
    """Eén vraag -> NDJSON-eventstream (start, [ping|delta]*, sources,
    done|error); start/ping zijn proxy-keepalives, de browser negeert ze.
    Validatie en rate limit lopen vóór de stream start; de vraag zelf wordt
    door assistant.py geauditeerd (wie/vraag/antwoord/bronnen)."""
    data = request.get_json(silent=True) or {}
    user = current_user()
    try:
        stream = assistant.ask_stream(data.get("question", ""), user)
    except assistant.AssistantError as exc:
        app.logger.warning("assistant geweigerd: user=%s status=%s reden=%s",
                           user, exc.http_status, exc)
        return {"errors": [str(exc)]}, exc.http_status
    app.logger.info("assistant vraag gestart: user=%s", user)

    def ndjson():
        for event in stream:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return Response(ndjson(), mimetype="application/x-ndjson")


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8081")))
