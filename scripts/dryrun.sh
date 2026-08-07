#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/dryrun.sh — draait sectie 6 van de change tenant-onboarding-completion
# van begin tot eind: één invocatie in plaats van een reeks losse commando's.
#
# Het script doet alles wat vanaf een shell kan en stopt alleen waar een
# browser echt nodig is. Dat zijn twee momenten, allebei één klik:
#   1. de wegwerptenant aanmaken in het formulier — dát is de test van taak
#      6.1/6.2, dus het bestand hier met de hand schrijven zou de test leeg
#      maken;
#   2. de knop "wachtwoordlink" indrukken; de URL plak je terug en dit script
#      controleert de eenmaligheid zelf.
#
# Alles daartussen — wachten op Argo, de checks, het certificaat, de tweede
# lezing, het opruimplan — gaat vanzelf.
#
# Writes: standaard niets. Met --seed-cert schrijft het één TLS-Secret in de
#   namespace van de wegwerptenant (zelfondertekend, 2 dagen geldig).
# Idempotent: yes — elke stap kijkt eerst of hij al gedaan is.
# Requires: kubectl, curl, openssl, python3, jq; gh voor de PR-controle
#   (ontbreekt gh, dan slaat die stap over in plaats van te falen)
#
# Usage:
#   ./scripts/dryrun.sh                                  # met de standaardwaarden
#   ./scripts/dryrun.sh --tenant dryrun-accept --seed-cert
#   ./scripts/dryrun.sh --skip-reveal                    # alleen 6.1 en 6.2
#   TENANT_WAIT=600 ./scripts/dryrun.sh                  # langer wachten op Argo

set -euo pipefail

cd "$(dirname "$0")/.."

readonly PORTAL_URL="${PORTAL_URL:-https://platform.commonground.nu}"
readonly TENANTS_REPO="${TENANTS_REPO:-ConductionNL/Nextcloud-base}"
readonly TENANT_WAIT="${TENANT_WAIT:-900}"     # seconden wachten op de namespace
readonly POLL="${POLL:-15}"

tenant="${TENANT:-dryrun-accept}"
theme="${THEME:-dryrun-theme}"
host="${HOST:-dryrun.example.org}"
seed_cert=false
skip_reveal=false

say()  { printf '%s\n' "$1"; }
head2() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
ask()  { printf '\n\033[33m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFOUT\033[0m %s\n' "$1" >&2; }

usage() { sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

pause_for() {
  ask "$1"
  printf '   Druk op Enter als dat gedaan is (Ctrl-C om te stoppen)… '
  read -r _
}

# Vóór het wachten: is er überhaupt iets aangevraagd? Zonder deze check staat
# het script een kwartier te wachten op een namespace die nooit komt, terwijl
# het antwoord ("het formulier heeft niets ingediend") binnen een seconde te
# geven is. Precies dat gebeurde op 2026-08-07.
check_tenant_pr() {
  if ! command -v gh >/dev/null 2>&1; then
    say "  (gh niet beschikbaar — PR-controle overgeslagen)"
    return 0
  fi
  local state
  state="$(gh pr list --repo "${TENANTS_REPO}" --state all --limit 5 \
    --head "add-tenant/${tenant}" --json state --jq '.[0].state' 2>/dev/null || echo "")"
  case "${state}" in
    MERGED)
      ok "aanvraag voor ${tenant} is gemerged"
      return 0 ;;
    OPEN)
      bad "de aanvraag voor ${tenant} staat nog OPEN op ${TENANTS_REPO}"
      say  "  Merge hem eerst; Argo maakt de namespace daarna vanzelf aan."
      say  "  gh pr list --repo ${TENANTS_REPO} --head add-tenant/${tenant}"
      return 1 ;;
    CLOSED)
      bad "de aanvraag voor ${tenant} is gesloten zonder merge"
      return 1 ;;
    *)
      bad "er is geen aanvraag add-tenant/${tenant} op ${TENANTS_REPO}"
      say  "  Het formulier heeft dus niets ingediend. Kijk wat er op de pagina"
      say  "  stond toen je op 'Aanvraag indienen' drukte — een foutmelding daar"
      say  "  is de bevinding, niet iets om omheen te werken."
      return 1 ;;
  esac
}

wait_for_namespace() {
  local waited=0
  if kubectl get namespace "${tenant}" >/dev/null 2>&1; then
    ok "namespace ${tenant} bestaat al"
    return 0
  fi
  say "  wachten tot Argo namespace ${tenant} aanmaakt (max ${TENANT_WAIT}s)…"
  while (( waited < TENANT_WAIT )); do
    if kubectl get namespace "${tenant}" >/dev/null 2>&1; then
      ok "namespace ${tenant} verschenen na ${waited}s"
      return 0
    fi
    sleep "${POLL}"
    waited=$(( waited + POLL ))
  done
  bad "namespace ${tenant} is er na ${TENANT_WAIT}s nog niet"
  say "  Is de tenant-PR op Nextcloud-base gemerged? Argo synct daarna vanzelf."
  return 1
}

seed_certificate() {
  local secret
  secret="$(printf '%s-tls\n' "$(printf '%s' "${host,,}" | sed 's/[^a-z0-9-]/-/g; s/^-*//; s/-*$//')")"
  if kubectl -n "${tenant}" get secret "${secret}" >/dev/null 2>&1; then
    ok "secret ${secret} bestaat al"
    return 0
  fi
  local dir
  dir="$(mktemp -d)"
  # Zelfondertekend en kort geldig: we testen de plumbing van formulier tot
  # handshake, niet een CA.
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 \
    -subj "/CN=${host}" -keyout "${dir}/tls.key" -out "${dir}/tls.crt" 2>/dev/null
  kubectl create secret tls "${secret}" -n "${tenant}" \
    --cert="${dir}/tls.crt" --key="${dir}/tls.key" >/dev/null
  rm -rf "${dir}"
  ok "secret ${secret} gezaaid (zelfondertekend, 2 dagen)"
}

check_reveal_link() {
  local url="$1" first second
  first="$(curl -s -o /dev/null -w '%{http_code}' "${url}")"
  if [[ "${first}" == "200" ]]; then
    ok "eerste opvraging: 200 — het wachtwoord is getoond"
  else
    bad "eerste opvraging gaf ${first}, verwacht 200"
    return 1
  fi
  second="$(curl -s -o /dev/null -w '%{http_code}' "${url}")"
  if [[ "${second}" == "404" ]]; then
    ok "tweede opvraging: 404 — de link is verbrand"
  else
    bad "tweede opvraging gaf ${second}, verwacht 404 — de link is HERBRUIKBAAR"
    return 1
  fi
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tenant) tenant="${2:-}"; shift 2 ;;
      --theme) theme="${2:-}"; shift 2 ;;
      --host) host="${2:-}"; shift 2 ;;
      --seed-cert) seed_cert=true; shift ;;
      --skip-reveal) skip_reveal=true; shift ;;
      -h|--help) usage; return 0 ;;
      *) echo "onbekende optie: $1" >&2; usage >&2; return 2 ;;
    esac
  done

  head2 "0. Preflight"
  ./scripts/verify-onboarding.sh --preflight || {
    bad "preflight faalt — los dat eerst op, de rest bouwt hierop"
    return 1
  }

  head2 "1. Wegwerptenant aanmaken"
  if kubectl get namespace "${tenant}" >/dev/null 2>&1; then
    ok "namespace ${tenant} bestaat al — stap overslaan"
  else
    pause_for "Open ${PORTAL_URL}/tenant en vul in:
     organisatie      : ${tenant%-*}
     omgeving         : ${tenant##*-}
     Geavanceerd → host : ${host}
     NL Design-thema    : ${theme}
     certificaat        : standaard laten
   Dien in en merge de PR op Nextcloud-base.
   (Dit moet via het formulier: dát is wat taak 6.1/6.2 test.)"
    check_tenant_pr || return 1
    wait_for_namespace || return 1
  fi

  head2 "2. Wachten tot de frontend er staat"
  local waited=0
  until kubectl -n "${tenant}" get deploy >/dev/null 2>&1 \
        && [[ "$(kubectl -n "${tenant}" get deploy -o name 2>/dev/null | wc -l)" -gt 0 ]]; do
    (( waited >= TENANT_WAIT )) && { bad "geen deployments in ${tenant}"; return 1; }
    sleep "${POLL}"; waited=$(( waited + POLL ))
  done
  ok "deployments aanwezig na ${waited}s"

  if [[ "${seed_cert}" == true ]]; then
    head2 "3. Certificaat zaaien"
    seed_certificate
  fi

  head2 "4. Controles (taak 6.1, 6.2, 6.3-rechten)"
  ./scripts/verify-onboarding.sh --tenant "${tenant}" --theme "${theme}" --host "${host}" || true

  if [[ "${skip_reveal}" == false ]]; then
    head2 "5. Eenmalige wachtwoordlink"
    ask "Ga naar ${PORTAL_URL}/ , klik bij ${tenant} op 'wachtwoordlink',
   en plak de URL hieronder. Open hem NIET zelf — dit script test hem."
    printf '   URL: '
    local url; read -r url
    if [[ -z "${url}" ]]; then
      say "  overgeslagen"
    else
      check_reveal_link "${url}" || true
    fi
  fi

  head2 "6. Opruimen"
  say "  Verwijder eerst het tenantbestand via ${PORTAL_URL}/tenant/delete?tenant=${tenant}"
  say "  en merge die PR. Draai daarna:"
  say ""
  say "    ./scripts/cleanup-tenant.sh --tenant ${tenant}            # plan"
  say "    ./scripts/cleanup-tenant.sh --tenant ${tenant} --execute  # uitvoeren"
  say ""
  say "Klaar. Wat hierboven op FOUT staat is de uitkomst van de dry-run."
}

main "$@"
