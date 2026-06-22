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

from flask import Flask, Response, render_template, request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import provision_gui  # noqa: E402  — reuse the tested build_command()

# Tenant creation (Phase 3): render + validate a Nextcloud-base tenant file and
# open it as a PR. gitlib/tenants live alongside this module (webgui/).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitlib    # noqa: E402
import tenants   # noqa: E402

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
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return "ok\n", 200, {"Content-Type": "text/plain"}


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
    fields = {
        "name": form.get("name", ""),
        "environment": form.get("environment", ""),
        "dbType": form.get("dbType", ""),
        "wave": form.get("wave", "1"),
        "apps": form.getlist("apps"),
        "frontend_host": form.get("frontend_host", ""),
        "frontend_org": form.get("frontend_org", ""),
    }

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
    return {"pr_url": result.get("html_url"), "pr_number": result.get("number")}, 201


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8081")))
