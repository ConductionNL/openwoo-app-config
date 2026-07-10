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
import hashlib   # noqa: E402
import json      # noqa: E402
import re        # noqa: E402

TENANTS_DIR = "nextcloud-platform/values/tenants"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# Fail closed by default: refuse any request without an oauth2-proxy identity.
# Set REQUIRE_AUTH=false only for local dev where no proxy fronts the app.
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "true").strip().lower() not in (
    "false", "0", "no", "off")


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
    because oauth2-proxy is the sole ingress — see the module docstring."""
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
    try:
        argv, env = provision_gui.build_command(values)
    except ValueError as exc:
        return Response(f"error: {exc}\n", status=400, mimetype="text/plain")

    user = current_user()
    # Audit: who + what + options. NEVER the password/apikey (they live only in env).
    app.logger.info("provision requested: user=%s base=%s run_syncs=%s force_import=%s",
                    user, values["base"], values["run_syncs"], values["force_import"])

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
    return render_template("tenant.html")


@app.post("/tenant")
def tenant_create():
    """Validate the form, render tenant-<name>.yaml, and open a PR on the tenants
    repo as the token's identity. The operator (oauth2-proxy) is stamped as
    requested-by; the merge stays a human gate. Returns JSON {pr_url, pr_number}.

    The portal NEVER creates secrets or touches the cluster — tenant secrets are
    generated in-cluster (ESO). Its only privileged action is opening this PR."""
    form = request.form
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
    )
    # defense-in-depth: the derived fields must still pass the full validator
    errors = tenants.validate(fields)
    if errors:
        return {"errors": errors}, 400

    user = current_user()
    name = fields["name"].strip()
    path = f"nextcloud-platform/values/tenants/{tenants.filename(name)}"
    content = tenants.render(fields)
    branch = f"add-tenant/{name}"
    commit_msg = (f"add tenant: {name}\n\n"
                  f"Opened from the OpenWoo provisioning portal.\n"
                  f"requested-by: {user}\n")
    pr_body = (f"Adds tenant `{name}` via the OpenWoo provisioning portal.\n\n"
               f"- requested-by: `{user}`\n"
               f"- machine-authored: review before merge.\n")

    app.logger.info("tenant PR requested: user=%s name=%s env=%s db=%s",
                    user, name, fields["environment"], fields["dbType"])
    try:
        result = gitlib.propose_file(
            branch=branch, path=path, content=content,
            commit_message=commit_msg, pr_title=f"add tenant: {name}", pr_body=pr_body)
    except gitlib.GitlibError as exc:
        # 409 = branch/file already exists (tenant in flight); 0 = misconfig/unreachable.
        status = 409 if exc.status == 409 else (502 if exc.status in (0, 500, 502, 503) else 400)
        app.logger.warning("tenant PR failed: user=%s name=%s status=%s detail=%s",
                            user, name, exc.status, exc.detail)
        return {"errors": [exc.detail]}, status

    app.logger.info("tenant PR opened: user=%s name=%s pr=%s", user, name, result.get("number"))
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number"),
            "tenant": name}, 201


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
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number"),
            "count": len(names), "tenants": names}, 201


@app.get("/tenant/delete")
def tenant_delete_form():
    return render_template("delete.html", tenant=request.args.get("tenant", ""))


@app.post("/tenant/delete")
def tenant_delete():
    """Open a PR that REMOVES a tenant file. NB: Argo prunes the Nextcloud app on
    merge, but PV/PVCs and the <tenant>-reactfront app are NOT auto-removed —
    flagged in the PR body for manual cleanup (destructive, human-reviewed)."""
    tenant = request.form.get("tenant", "").strip()
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", tenant):
        return {"errors": ["invalid tenant name"]}, 400
    user = current_user()
    path = f"{TENANTS_DIR}/{tenants.filename(tenant)}"
    branch = f"delete-tenant/{tenant}"
    commit_msg = f"remove tenant: {tenant}\n\nrequested-by: {user}\n"
    pr_body = (f"Removes tenant `{tenant}` via the OpenWoo portal.\n\n"
               f"- requested-by: `{user}`\n"
               f"- ⚠️ After merge Argo prunes the Nextcloud app, but **PV/PVCs and the "
               f"`{tenant}-reactfront` frontend app (preserveResourcesOnDeletion) are NOT "
               f"auto-removed** — clean those up manually.\n")
    app.logger.info("delete PR requested: user=%s tenant=%s", user, tenant)
    try:
        result = gitlib.propose_deletion(branch=branch, path=path, commit_message=commit_msg,
                                         pr_title=f"remove tenant: {tenant}", pr_body=pr_body)
    except gitlib.GitlibError as exc:
        status = (404 if exc.status == 404 else 409 if exc.status == 409
                  else 502 if exc.status in (0, 500, 502, 503) else 400)
        return {"errors": [exc.detail]}, status
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
    can show a green check before handing off to provisioning."""
    tenant = request.args.get("tenant", "")
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", tenant):
        return {"errors": ["invalid tenant name"]}, 400
    try:
        return argolib.app_status(f"nc-{tenant}"), 200
    except argolib.ArgoError as exc:
        return {"errors": [exc.detail]}, 502


@app.get("/dashboard.json")
def dashboard_data():
    """Landing-page overview: tenant Argo apps (nc-*) + recent tenant PRs. Each
    source fails independently (partial errors reported) so the page still loads."""
    out = {"tenants": [], "prs": [], "errors": []}
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
    """Eén vraag -> NDJSON-eventstream (delta*, sources, done|error).
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
