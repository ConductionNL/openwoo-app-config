#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/provision.py — tenant provisioning + validation over the API (entrypoint).
#
# The "target track" of this repo: drive a running Nextcloud tenant
# (OpenRegister / OpenConnector / OpenCatalogi) into the state the WOO config
# describes, and assert each step took effect — "test what you ship". Driven by
# the same sanitized config, so the entities it touches are exactly the config's.
# Idempotent convergence: every step is an upsert/PUT to the desired state, so it
# is safe to re-run (it does not wipe, and does not prune entities absent from
# the config).
#
# This file is a THIN ENTRYPOINT. The logic lives in the `provisionlib` package
# (constants / helpers / client / steps / cli) so each concern is a small,
# auditable, separately-tested module. Callers that `import provisionlib as
# provision` get the same surface; the GUI and webgui shell out to this script.
#
# Subcommands:
#   settings       — PUT OpenRegister organisation + multitenancy settings
#   oc-settings    — couple OpenCatalogi object types (catalog/listing/...) to
#                    their register + schema (slugs resolved to tenant ids)
#   import         — upload the config and assert "Import successful"
#   verify-import  — assert every config slug (registers/schemas/sources/syncs/
#                    jobs) is present on the tenant (catches silent partial import)
#   authorization  — (repair) enforce schema authorization flags (inheritFromPublic)
#   catalog        — point the OpenCatalogi catalog at the WOO register + schemas
#   delete-menu    — delete the OpenCatalogi default "User Menu" object
#   credentials    — set each source's API-key header and assert it reflects
#   sync-check     — assert every sync resolved its target schema (no dangling)
#   sync-run       — POST run/--test per synchronization (real run fetches data)
#   objects        — create one object in a register/schema from a JSON payload
#   all            — the full bring-up in order, gating each step
#
# Auth: basic-auth (admin user / app password). Credentials come from
# --password / --password-env / an interactive getpass prompt and the source key
# from --apikey / --apikey-env — never argv-only secrets, never logged. Real
# credentials live in a K8s secret / ESO, never in the committed config.
#
# Pure Python standard library — no third-party dependencies, by design
# (auditability + no supply-chain surface). Mirrors scripts/oac.py.
#
# Writes: read-only on the repo; mutates the *target tenant*.
# Idempotent: yes — re-running converges to the same state and re-asserts.
# Requires: python3.8+, a running instance reachable at --base.
# NOTE: the list endpoints are assumed to return all rows (unpaginated); verified
#   against the current apps. If a future version paginates, verify-import /
#   catalog / oc-settings would need a limit/paging parameter.
#
# Usage:
#   python3 scripts/provision.py all \
#       --base https://<tenant> --user admin --apikey-env OPENWOO_APIKEY   # prompts for password
#   python3 scripts/provision.py verify-import --base ... --user ... --password-env PW
#   python3 scripts/provision.py credentials --base ... --user ... --apikey-env OPENWOO_APIKEY
"""Entrypoint for the provisioning lib — delegates to provisionlib.cli.main()."""

import sys
from pathlib import Path

# Allow `python3 scripts/provision.py ...` to find the sibling package whatever
# the caller's cwd (the GUI/webgui and functional-test.sh shell out by path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from provisionlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
