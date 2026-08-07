#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/cleanup-tenant.sh — ruim een wegwerptenant volledig op.
#
# Het tenantbestand uit git halen laat het meeste staan. De ApplicationSet zet
# `preserveResourcesOnDeletion: true`, dus Argo verwijdert alleen de
# Application `nc-<tenant>`; de namespace, PVC's en secrets blijven. Dat is een
# bewuste veiligheidsklep (Nextcloud-base docs/TENANT-OPERATIONS.md) en precies
# de reden dat opruimen na een dry-run handwerk is dat je vergeet.
#
# Dit script inventariseert wat er nog staat en zegt wat elke stap weghaalt.
# Zonder --execute verandert het niets: dan is het een plan dat je kunt lezen
# voordat je het uitvoert.
#
# LET OP wat het NIET opruimt (kan het ook niet zien):
#   * S3-data in de bucket van de tenant — zie Nextcloud-base STORAGE-OPERATIONS.md
#   * DNS-records
#   * een eventueel handgezaaid TLS-secret dat je elders bewaarde
#
# Writes: niets zonder --execute. Met --execute: verwijdert Argo-Applications en
#   de namespace van één tenant (cascade: PVC's, secrets, deployments).
# Idempotent: yes — wat al weg is wordt overgeslagen.
# Requires: kubectl met clustertoegang
#
# Usage:
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept              # plan tonen
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept --execute    # uitvoeren, met bevestiging
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept --execute --yes
#   ARGO_NS=argocd ./scripts/cleanup-tenant.sh --tenant dryrun-accept

set -euo pipefail

readonly ARGO_NS="${ARGO_NS:-argocd}"

tenant=""
execute=false
assume_yes=false
force_prod=false

say()  { printf '%s\n' "$1"; }
warn() { printf '  ! %s\n' "$1" >&2; }
step() { printf '\n%s\n' "$1"; }

usage() {
  sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

# Voert uit, of toont alleen — één plek zodat plan en uitvoering nooit uiteen
# kunnen lopen: wat je in het plan ziet is letterlijk wat er draait.
run() {
  if [[ "${execute}" == true ]]; then
    printf '  → %s\n' "$*"
    "$@"
  else
    printf '  %s\n' "$*"
  fi
}

# Zowel de Nextcloud-app (`nc-<tenant>`) als de losse frontend
# (`<tenant>-reactfront`). Die tweede is degene die bij handmatig opruimen
# blijft staan, want hij heet niet naar het patroon van de eerste.
tenant_apps() {
  kubectl -n "${ARGO_NS}" get applications -o name 2>/dev/null \
    | grep -E "/(nc-)?${tenant}(-reactfront)?$" || true
}

inventory() {
  local ns_state pvcs
  local -a apps=()
  ns_state="$(kubectl get namespace "${tenant}" -o jsonpath='{.status.phase}' 2>/dev/null || echo "-")"
  mapfile -t apps < <(tenant_apps)
  pvcs="$(kubectl -n "${tenant}" get pvc --no-headers 2>/dev/null | wc -l || echo 0)"

  step "Gevonden voor '${tenant}':"
  say  "  namespace      : ${ns_state}"
  say  "  PVC's          : ${pvcs}"
  if [[ "${#apps[@]}" -gt 0 ]]; then
    say "  Argo-apps      :"
    printf '    %s\n' "${apps[@]}"
  else
    say "  Argo-apps      : geen"
  fi
  if [[ "${ns_state}" == "-" && "${#apps[@]}" -eq 0 ]]; then
    say ""
    say "Niets te doen — alles is al opgeruimd."
    return 1
  fi
  return 0
}

plan() {
  step "1. Tenantbestand uit git (doe dit EERST, en apart)"
  say  "  Via het portaal: Omgeving verwijderen → opent een PR die"
  say  "  nextcloud-platform/values/tenants/tenant-${tenant}.yaml weghaalt."
  say  "  Na de merge verwijdert Argo de Application nc-${tenant}, maar laat"
  say  "  de namespace staan. Loopt dit script vóór die merge, dan zet de"
  say  "  ApplicationSet alles gewoon terug."

  step "2. Argo-Applications weghalen"
  local -a apps=()
  local app
  mapfile -t apps < <(tenant_apps)
  if [[ "${#apps[@]}" -eq 0 ]]; then
    say "  (geen)"
  else
    for app in "${apps[@]}"; do
      run kubectl -n "${ARGO_NS}" delete "${app}"
    done
  fi

  step "3. Namespace weg (cascade: PVC's, secrets, deployments)"
  run kubectl delete namespace "${tenant}"

  step "4. Handmatig, buiten dit script"
  say  "  - S3-data van de tenant (STORAGE-OPERATIONS.md)"
  say  "  - DNS-records voor de eigen host, als die zijn gezet"
  say  "  - een reveal-ticket blijft hooguit tot zijn TTL staan en is daarna"
  say  "    onschadelijk: het wachtwoord staat er niet in en het secret is weg"
}

confirm() {
  [[ "${assume_yes}" == true ]] && return 0
  printf '\nDit verwijdert de namespace %s inclusief alle PVC-data. Typ de naam om te bevestigen: ' "${tenant}"
  local answer
  read -r answer
  [[ "${answer}" == "${tenant}" ]] || { echo "afgebroken" >&2; return 1; }
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tenant) tenant="${2:-}"; shift 2 ;;
      --execute) execute=true; shift ;;
      --yes) assume_yes=true; shift ;;
      --force-production) force_prod=true; shift ;;
      -h|--help) usage; return 0 ;;
      *) echo "onbekende optie: $1" >&2; usage >&2; return 2 ;;
    esac
  done

  if [[ -z "${tenant}" ]]; then
    echo "geef --tenant <naam>" >&2; usage >&2; return 2
  fi
  if [[ ! "${tenant}" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    echo "ongeldige tenantnaam: ${tenant}" >&2; return 2
  fi
  # Een productietenant opruimen is bijna nooit wat je bedoelt, en de kosten van
  # het per ongeluk doen zijn niet terug te draaien.
  if [[ "${tenant}" == *-prod && "${force_prod}" != true ]]; then
    echo "'${tenant}' ziet eruit als productie. Voeg --force-production toe als je dit echt bedoelt." >&2
    return 2
  fi

  inventory || return 0

  # Het plan wordt ALTIJD eerst getoond, ook bij --execute. Je ziet dus precies
  # wat er gaat gebeuren voordat je bevestigt, en `run()` zorgt dat de getoonde
  # commando's letterlijk dezelfde zijn als de uitgevoerde.
  local want_execute="${execute}"
  execute=false
  plan

  if [[ "${want_execute}" == false ]]; then
    step "Dit was een plan; er is niets gewijzigd."
    say  "Voer het uit met --execute (of --execute --yes om de bevestiging over te slaan)."
    return 0
  fi

  confirm || return 1
  execute=true
  step "Uitvoeren"
  plan
  step "Klaar. Controleer met: kubectl get ns ${tenant}"
}

main "$@"
