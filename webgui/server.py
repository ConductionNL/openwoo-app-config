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
# Auth: NONE in Phase 1 (run locally / behind a trusted network). Phase 2 puts it
# behind oauth2-proxy → Keycloak (which brokers Google); the app then reads the
# operator identity from the proxy header (current_user()).
#
# Writes: read-only on the repo; the spawned provision.py mutates the *target
#   tenant* (the URL the operator enters). Secrets are never logged.
# Requires: python3.8+, Flask (webgui/requirements.txt), network egress to the
#   tenant's public URL.
#
# Usage:
#   pip install -r webgui/requirements.txt
#   python3 webgui/server.py            # http://127.0.0.1:8081
#   PORT=9000 python3 webgui/server.py
"""Hosted provisioning control-plane (Flask, Phase 1)."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, Response, render_template, request

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import provision_gui  # noqa: E402  — reuse the tested build_command()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
app = Flask(__name__)
app.logger.setLevel(logging.INFO)


def current_user():
    """Operator identity. Phase 2 (oauth2-proxy) sets these headers; Phase 1 has
    no auth so it falls back to '-'."""
    return (request.headers.get("X-Forwarded-Email")
            or request.headers.get("X-Forwarded-User")
            or "-")


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


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8081")))
