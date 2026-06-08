#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/functional-test.sh — prove a config imports into a clean OpenRegister.
#
# Spins up an ephemeral Nextcloud + PostgreSQL (docker-compose.test.yml),
# installs the Conduction apps, imports the sanitized config via the
# OpenRegister API, and asserts the import succeeds. Tears the stack down
# afterwards (volumes wiped), so every run starts from an empty install — the
# point being to prove a clean tenant accepts this config without errors.
#
# Layer-2 functional test, complementing the static `oac.py lint` gate.
#
# Writes: read-only on the repo; creates+destroys docker containers/volumes.
# Idempotent: yes — each run is a fresh stack.
# Requires: docker (with compose plugin), curl. NOTE: needs a docker-capable
#   host; Codeberg's shared Woodpecker runners typically cannot run this, so it
#   is a local / self-hosted-nightly check, not a per-PR Codeberg gate.
#
# Usage:
#   ./scripts/functional-test.sh                       # default config
#   CONFIG=config/woo.configuration.json ./scripts/functional-test.sh
#   KEEP_UP=1 ./scripts/functional-test.sh             # leave stack running to debug
set -euo pipefail

readonly COMPOSE_FILE="docker-compose.test.yml"
readonly BASE_URL="http://localhost:8080"
readonly ADMIN="admin:admin_test_only"
readonly ADMIN_USER="admin"
readonly ADMIN_PASS="admin_test_only"
readonly APPS=(openregister openconnector opencatalogi)
readonly CONFIG="${CONFIG:-config/woo.configuration.json}"
readonly KEEP_UP="${KEEP_UP:-0}"

# Detect an available compose implementation (this env uses podman-compose;
# Codeberg/CI may differ). Order: docker compose, podman compose, podman-compose.
COMPOSE=()
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
  elif podman compose version >/dev/null 2>&1; then
    COMPOSE=(podman compose)
  elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE=(podman-compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    die "no compose implementation found (docker compose / podman compose / podman-compose)"
  fi
  log "using compose: ${COMPOSE[*]}"
}
compose() { "${COMPOSE[@]}" -f "${COMPOSE_FILE}" "$@"; }
occ() { compose exec -T -u www-data nextcloud php occ "$@"; }
log() { echo "==> $*" >&2; }
die() { echo "error: $*" >&2; exit 1; }

cleanup() {
  if [[ "${KEEP_UP}" == "1" ]]; then
    log "KEEP_UP=1 — leaving stack running (${BASE_URL}); 'docker compose -f ${COMPOSE_FILE} down -v' to clean"
    return
  fi
  log "tearing down stack (volumes wiped)"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}

wait_for_install() {
  log "waiting for Nextcloud to finish auto-install"
  local _
  for _ in $(seq 1 60); do
    if curl -fsS "${BASE_URL}/status.php" 2>/dev/null | grep -q '"installed":true'; then
      log "Nextcloud installed"
      return 0
    fi
    sleep 5
  done
  die "Nextcloud did not become ready in time"
}

install_apps() {
  local app
  for app in "${APPS[@]}"; do
    log "installing app: ${app}"
    occ app:install "${app}" 2>/dev/null || occ app:enable "${app}" \
      || die "could not install/enable ${app} (is it on the appstore for this NC version?)"
  done
}

import_config() {
  [[ -f "${CONFIG}" ]] || die "config not found: ${CONFIG}"
  log "importing ${CONFIG} via OpenRegister API"
  local body status
  body="$(curl -sS -u "${ADMIN}" \
    -w '\n%{http_code}' \
    -F "file=@${CONFIG};type=application/json" \
    "${BASE_URL}/index.php/apps/openregister/api/configurations/import")"
  status="$(echo "${body}" | tail -n1)"
  body="$(echo "${body}" | sed '$d')"
  echo "${body}" >&2
  [[ "${status}" == "200" ]] || die "import returned HTTP ${status}"
  echo "${body}" | grep -q 'Import successful' || die "import response missing success marker"
  log "import OK (HTTP 200, Import successful)"
}

# Map config bucket -> the table that should hold its rows after import.
# registers/schemas live in openregister; sources/syncs/mappings/rules in
# openconnector. Default Nextcloud table prefix is oc_.
verify_import() {
  log "verifying imported row counts against the config"
  local -A table=(
    [schemas]=oc_openregister_schemas
    [registers]=oc_openregister_registers
    [sources]=oc_openconnector_sources
    [mappings]=oc_openconnector_mappings
    [rules]=oc_openconnector_rules
    [synchronizations]=oc_openconnector_synchronizations
  )
  local bucket want got fail=0
  for bucket in "${!table[@]}"; do
    want="$(config_count "${bucket}")"
    got="$(db_count "${table[$bucket]}")"
    if [[ "${want}" == "${got}" ]]; then
      log "  ${bucket}: ${got} (expected ${want}) OK"
    else
      echo "  MISMATCH ${bucket}: imported ${got}, config has ${want}" >&2
      fail=1
    fi
  done
  [[ "${fail}" == "0" ]] || die "imported row counts do not match the config — import is incomplete"
}

config_count() {
  python3 - "${CONFIG}" "$1" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
bucket = doc.get("components", {}).get(sys.argv[2])
print(len(bucket) if isinstance(bucket, (list, dict)) else 0)
PY
}

db_count() {
  compose exec -T db psql -U nextcloud -d nextcloud -tAc "SELECT count(*) FROM $1;" 2>/dev/null | tr -d '[:space:]'
}

# Post-import provisioning step we ship: for every source in the config, PUT a
# dummy apikey via the API and assert it reflects (scripts/provision.py). Proves
# the credential-provisioning path against the freshly imported source. No real
# data fetch — the demo source is auth:none and the key is an obvious dummy.
provision_credentials() {
  log "provisioning source credentials (dummy apikey) via the API"
  python3 scripts/provision.py credentials \
    --base "${BASE_URL}" --user "${ADMIN_USER}" --password "${ADMIN_PASS}" \
    --config "${CONFIG}" \
    || die "credential provisioning failed"
}

main() {
  detect_compose
  trap cleanup EXIT
  log "starting ephemeral stack"
  compose up -d
  wait_for_install
  install_apps
  import_config
  verify_import
  provision_credentials
  log "FUNCTIONAL TEST PASSED"
}

main "$@"
