---
last_reviewed: 2026-07-06
owner: mark
---

# Provisioning a tenant

Any operator who can reach a tenant can run the target track — it is
pure-stdlib Python over HTTPS, no repo-specific state. Supply the tenant
URL and an admin / app-password credential (kept out of argv via env):

```bash
# one .env file per target (gitignored; see .env.canary.example)
#   CANARY_USER=...      CANARY_PASS=<app password>      OPENWOO_APIKEY=<source key>
set -a; . .env.<target>; set +a
python3 scripts/provision.py all \
    --base https://<tenant> --user "$CANARY_USER" \
    --password-env CANARY_PASS --apikey-env OPENWOO_APIKEY
```

Credentials can also come from **interactive prompts** — omit `--user`,
`--password` and `--apikey` and, on a terminal, the tool asks for the
admin user (default `admin`), the app password and the source API key
(`getpass`, never stored or in argv). The minimal fully-interactive run:

```bash
python3 scripts/provision.py all --base https://<tenant>
#   Nextcloud admin user [admin]:
#   App password for admin @ https://<tenant>:
#   Source API key (blank = dummy test key):
#   Source URL (blank = keep config default):
#   API-Interface-ID (blank = keep config default):
```

The source URL, API-Interface-ID and API key are **per-tenant** (each
client's source system differs), so they are supplied per run — not
committed to the config. Provisioning is **operator-driven**: an operator
runs the CLI/GUI against the tenant's public URL after a deployment (the
public host is a trusted domain, so it just works — no in-cluster wiring).

For a form-based front-end, `scripts/provision_gui.py` opens a small
Tkinter window with the same fields and runs `provision.py all` (secrets
passed via env, never argv). With no Tkinter/display it prints the
equivalent terminal command.

## What `all` does

`all` is a **convergence/repair** run: it updates the existing tenant's
objects to the config's desired state — it does not wipe, and does not
prune entities that exist on the tenant but not in the config. It is
**idempotent**: each step GET-checks first and skips the write when the
tenant is already correct, so a re-run on a converged tenant is a near
no-op. The config import is skipped when every slug is already present;
pass `--force-import` (or the GUI "Force re-import" checkbox) to
re-upload after a config *content* change. Run a single step
(`verify-import`, `sync-check`, …) to *validate* an existing tenant
without changing it — see the [command reference](provisioner-commands.md).
Credentials come from the operator's own env/secret, never from this repo.

## Source credentials

The demo source authenticates with an API key sent as the `API-KEY`
request header. The config ships an **empty placeholder**
(`configuration."headers.API-KEY": ""`) — the real key is **never
committed**; it is injected at provision time from a secret (a K8s
secret / ESO in prod, an env var locally):

```bash
# real provisioning against a tenant — password and key come from env vars
# (kept out of argv / logs); username is not a secret:
python3 scripts/provision.py credentials \
    --base https://<tenant> --user <admin> \
    --password-env CANARY_PASS --apikey-env OPENWOO_APIKEY
```

Real credentials for any source come from a secret store, never from the
config JSON or git history.
