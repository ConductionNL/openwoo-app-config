#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/provision_gui.py — a small Tkinter front-end for scripts/provision.py.
#
# Lets an operator fill in the per-tenant values (tenant URL, admin user, app
# password, source URL, API-Interface-ID, source API key) in a form and run the
# full bring-up (`provision.py all`). It is a thin wrapper: it shells out to
# provision.py, passing non-secrets as args and secrets via env vars (never
# argv), and streams the output into a text pane.
#
# Terminal route (no GUI needed): just run provision.py directly — it prompts for
# the same values on a terminal. This GUI is an optional alternative front-end;
# if Tkinter or a display is unavailable it prints the equivalent terminal
# command and exits.
#
# Pure Python standard library (Tkinter is stdlib). Mirrors scripts/provision.py.
#
# Writes: read-only on the repo; the spawned provision.py mutates the target tenant.
# Requires: python3.8+, Tkinter (for the GUI), a reachable tenant.
#
# Usage:
#   python3 scripts/provision_gui.py
#   python3 scripts/provision_gui.py   # then fill the form and press "Run provisioning"
"""Tkinter front-end for scripts/provision.py (per-tenant bring-up form)."""

import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

PROVISION = str(Path(__file__).resolve().parent / "provision.py")

# In-cluster provisioning target. Both the provisioner and every tenant Nextcloud
# run in the same cluster, so we can reach the tenant's internal Service over
# cluster-local DNS instead of its public `*.commonground.nu` record. The public
# record is published by external-dns with TTL=1 and can flap while a tenant's
# ingress is (re)created, which intermittently breaks a run with a DNS error;
# the internal Service name never flaps. See docs/design.md.
INCLUSTER_SERVICE = "nextcloud"        # Service name in the tenant namespace
INCLUSTER_PORT = "8080"                # Nextcloud http port on that Service
_PLATFORM_DOMAIN = ".commonground.nu"  # tenant public-host convention


def incluster_target(public_base):
    """Map a public tenant base URL to its in-cluster equivalent.

    Returns `(internal_base, host_header)` where `internal_base` points at the
    tenant's cluster-local Nextcloud Service and `host_header` is the original
    public hostname (a Nextcloud trusted_domain, so requests are accepted).

    The tenant namespace is `<org>-<env>`, derived from the public host per the
    platform convention (inverse of webgui/templates/tenant.html):
      - `<org>.commonground.nu`        -> org=<org>, env=prod
      - `<org>.<env>.commonground.nu`  -> org=<org>, env=<env>

    Returns None when the host is not a `*.commonground.nu` tenant host (caller
    then falls back to the public base unchanged).
    """
    host = urlsplit(public_base).hostname
    if not host or not host.endswith(_PLATFORM_DOMAIN):
        return None
    labels = host[: -len(_PLATFORM_DOMAIN)].split(".")
    if len(labels) == 1:
        org, env = labels[0], "prod"
    elif len(labels) == 2:
        org, env = labels[0], labels[1]
    else:
        return None
    if not org or not env:
        return None
    namespace = f"{org}-{env}"
    internal_base = (
        f"http://{INCLUSTER_SERVICE}.{namespace}.svc.cluster.local:{INCLUSTER_PORT}"
    )
    return internal_base, host

# (key, label, secret?, default) — the per-tenant inputs the form collects.
FIELDS = [
    ("base", "Tenant base URL", False, "https://<org>.accept.commonground.nu"),
    ("user", "Admin user", False, "admin"),
    ("password", "App password", True, ""),
    ("source_url", "Source URL (blank = keep config)", False, ""),
    ("api_interface_id", "API-Interface-ID (blank = keep config)", False, ""),
    ("apikey", "Source API key (blank = dummy)", True, ""),
    ("job_user", "Job user (blank = admin; Anonymous-bug workaround)", False, ""),
]


def build_command(values):
    """Build (argv, env) for `provision.py all` from collected form values.

    Non-secrets go on argv; secrets (password, apikey) go via env vars referenced
    by --password-env / --apikey-env so they never land in argv. Raises ValueError
    if the required base URL is missing.
    """
    base = (values.get("base") or "").strip()
    if not base or base.startswith("https://<") or base.startswith("http://<"):
        raise ValueError("Tenant base URL is required")
    # In-cluster mode: hit the tenant's internal Service (stable cluster-local DNS)
    # and present the public host as Host, instead of resolving the flaky public
    # record. Falls back to the public base when the host isn't a tenant host.
    connect_base, host_header = base, None
    if values.get("in_cluster"):
        target = incluster_target(base)
        if target:
            connect_base, host_header = target
    argv = [sys.executable, PROVISION, "all", "--base", connect_base]
    if host_header:
        argv += ["--host-header", host_header]
    if values.get("user"):
        argv += ["--user", values["user"].strip()]
    if values.get("source_url"):
        argv += ["--source-url", values["source_url"].strip()]
    if values.get("api_interface_id"):
        argv += ["--api-interface-id", values["api_interface_id"].strip()]
    if values.get("job_user"):
        argv += ["--job-user", values["job_user"].strip()]
    if values.get("force_import"):
        argv += ["--force-import"]
    if values.get("run_syncs"):
        argv += ["--run-syncs"]
        if values.get("dry_run"):
            argv += ["--test"]
    env = dict(os.environ)
    if values.get("password"):
        env["GUI_PROVISION_PASSWORD"] = values["password"]
        argv += ["--password-env", "GUI_PROVISION_PASSWORD"]
    if values.get("apikey"):
        env["GUI_PROVISION_APIKEY"] = values["apikey"]
        argv += ["--apikey-env", "GUI_PROVISION_APIKEY"]
    return argv, env


def _run_gui():
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("OpenWoo tenant provisioning")
    entries = {}
    for row, (key, label, secret, default) in enumerate(FIELDS):
        tk.Label(root, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=6, pady=3)
        entry = tk.Entry(root, width=48, show="*" if secret else "")
        entry.insert(0, default)
        entry.grid(row=row, column=1, padx=6, pady=3)
        entries[key] = entry

    force_import_var = tk.BooleanVar(value=False)
    run_syncs_var = tk.BooleanVar(value=False)
    dry_run_var = tk.BooleanVar(value=False)
    tk.Checkbutton(root, text="Force re-import (re-upload config even if already present)",
                   variable=force_import_var).grid(row=len(FIELDS), column=0, columnspan=2, sticky="w", padx=6)
    tk.Checkbutton(root, text="Run synchronizations after provisioning (fetches live data)",
                   variable=run_syncs_var).grid(row=len(FIELDS) + 1, column=0, columnspan=2, sticky="w", padx=6)
    tk.Checkbutton(root, text="    └ dry-run only (/test, no real fetch)",
                   variable=dry_run_var).grid(row=len(FIELDS) + 2, column=0, columnspan=2, sticky="w", padx=6)

    out = scrolledtext.ScrolledText(root, width=90, height=20)
    out.grid(row=len(FIELDS) + 4, column=0, columnspan=2, padx=6, pady=6)

    def append(line):
        out.insert("end", line)
        out.see("end")

    def worker(argv, env):
        try:
            proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                root.after(0, append, line)
            proc.wait()
            root.after(0, append, f"\n--- exit code {proc.returncode} ---\n")
        except Exception as exc:  # noqa: BLE001 - surface any spawn failure in the pane
            root.after(0, append, f"\nerror: {exc}\n")
        finally:
            root.after(0, lambda: run_btn.config(state="normal"))

    def on_run():
        values = {k: e.get() for k, e in entries.items()}
        values["force_import"] = force_import_var.get()
        values["run_syncs"] = run_syncs_var.get()
        values["dry_run"] = dry_run_var.get()
        try:
            argv, env = build_command(values)
        except ValueError as exc:
            append(f"error: {exc}\n")
            return
        run_btn.config(state="disabled")
        out.delete("1.0", "end")
        append(f"running: provision.py all --base {entries['base'].get()} ...\n\n")
        threading.Thread(target=worker, args=(argv, env), daemon=True).start()

    run_btn = tk.Button(root, text="Run provisioning", command=on_run)
    run_btn.grid(row=len(FIELDS) + 3, column=0, columnspan=2, pady=6)
    root.mainloop()


def main():
    try:
        _run_gui()
        return 0
    except Exception as exc:  # noqa: BLE001 - no Tkinter / no display: fall back to terminal
        print(f"GUI unavailable ({type(exc).__name__}: {exc}).", file=sys.stderr)
        print("Use the terminal route instead — it prompts for the same values:",
              file=sys.stderr)
        print(f"  python3 {PROVISION} all --base https://<tenant>", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
