#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/cleanup-tenant.sh — ruim een wegwerptenant volledig op.
#
# Het tenantbestand uit git halen ruimt minder op dan de naam van de vlag
# suggereert. `preserveResourcesOnDeletion: true` op de ApplicationSet bewaart de
# RESOURCES, niet de Application: zodra het tenantbestand weg is verwijdert de
# ApplicationSet-controller de Applications (`nc-<tenant>`, `<tenant>-reactfront`)
# gewoon — alleen zonder resources-finalizer, dus alles wat ze uitgerold hebben
# blijft draaien. Zie Nextcloud-base docs/TENANT-OPERATIONS.md,
# § Tenant Volledig Verwijderen.
#
# Gevolg voor dít script: stap 2 (Applications weghalen) is ná een gemergede
# verwijder-PR meestal een no-op — die zijn dan al opgeruimd. De échte wees zit
# in de namespace: het frontend-Deployment met zijn Ingress blijft verkeer
# serveren alsof er niets gebeurd is. Dat stopt pas bij stap 3, de
# namespace-delete. Draai dit script dus ook als stap 2 niets vindt.
#
# Dit script inventariseert wat er nog staat en zegt wat elke stap weghaalt.
# Zonder --execute verandert het niets: dan is het een plan dat je kunt lezen
# voordat je het uitvoert.
#
# LET OP wat het NIET opruimt (kan het ook niet zien):
#   * S3-data in de bucket van de tenant — zie Nextcloud-base STORAGE-OPERATIONS.md
#   * een eventueel handgezaaid TLS-secret dat je elders bewaarde
#
# DNS is géén handwerk. external-dns draait met `policy: sync` en bezit de
# records die het zelf aanmaakte (cluster-infra external-dns/values.yaml), dus
# het Cloudflare-record verdwijnt vanzelf zodra de Ingress weg is — precies wat
# stap 3 doet. Staat het record er later nog, dan staat de Ingress er ook nog;
# dat is de bevinding, niet het record. Zo staat het ook in React-base
# docs/ADDING-TENANT.md, § Frontend uitzetten of tenant verwijderen.
#
# Writes: niets zonder --execute. Met --execute: verwijdert Argo-Applications en
#   de namespace van één tenant (cascade: PVC's, secrets, deployments).
# Idempotent: yes — wat al weg is wordt overgeslagen.
# Requires: kubectl met clustertoegang. Env-instelbaar (geen hardcoded limieten):
#   ARGO_NS        namespace van Argo CD (default: argocd)
#   PROD_PATTERN   ERE tegen de tenantnaam; matcht hij, dan geldt de tenant als
#                  productie en is --force-production nodig
#                  (default: -(prod|production)$)
#   PROD_TENANTS   spatie-gescheiden extra namen die als productie gelden, voor
#                  tenants die buiten PROD_PATTERN vallen (default: leeg)
#
# Usage:
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept              # plan tonen
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept --execute    # uitvoeren, met bevestiging
#   ./scripts/cleanup-tenant.sh --tenant dryrun-accept --execute --yes
#   ARGO_NS=argocd ./scripts/cleanup-tenant.sh --tenant dryrun-accept
#   PROD_TENANTS="klant-een klant-twee" ./scripts/cleanup-tenant.sh --tenant klant-een

set -euo pipefail

readonly ARGO_NS="${ARGO_NS:-argocd}"

# Welke namen als productie gelden. Env-instelbaar omdat de `-prod`-naamconventie
# een afspraak is en geen garantie: een productietenant die morgen anders heet
# moet af te schermen zijn zonder dit script te wijzigen.
readonly DEFAULT_PROD_PATTERN='-(prod|production)$'
readonly PROD_PATTERN="${PROD_PATTERN:-${DEFAULT_PROD_PATTERN}}"
readonly PROD_TENANTS="${PROD_TENANTS:-}"

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
  say  "  Procedure: Nextcloud-base docs/REMOVING-TENANT.md — die is leidend."
  say  "  Kern: het portaal opent de PR; draai dit script pas NA de merge,"
  say  "  anders zet de ApplicationSet alles terug."

  step "2. Argo-Applications weghalen"
  say  "  (na een gemergede verwijder-PR meestal leeg: de ApplicationSet heeft"
  say  "   ze dan zelf al verwijderd — dat is normaal, ga door naar stap 3)"
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
  say  "  Dit is de stap die het frontend-Deployment en zijn Ingress stopt; tot"
  say  "  hier serveert de site van ${tenant} gewoon verkeer."
  run kubectl delete namespace "${tenant}"

  step "4. Handmatig, buiten dit script"
  say  "  - S3-data van de tenant (STORAGE-OPERATIONS.md)"
  say  "  - een reveal-ticket blijft hooguit tot zijn TTL staan en is daarna"
  say  "    onschadelijk: het wachtwoord staat er niet in en het secret is weg"
  say  "  DNS hoeft niet: external-dns (policy: sync) bezit het Cloudflare-record"
  say  "  en haalt het weg zodra de Ingress uit stap 3 verdwenen is."
}

# Waar of de tenant als productie geldt. Twee wegen, want een naamconventie
# dekt niet alles: een patroon (PROD_PATTERN) voor de regel, en een expliciete
# lijst (PROD_TENANTS) voor de uitzonderingen die niet aan de regel voldoen.
looks_like_production() {
  local -a extra=()
  local name
  if [[ "${tenant}" =~ ${PROD_PATTERN} ]]; then
    return 0
  fi
  if [[ -n "${PROD_TENANTS}" ]]; then
    read -r -a extra <<<"${PROD_TENANTS}"
    for name in "${extra[@]}"; do
      if [[ "${tenant}" == "${name}" ]]; then
        return 0
      fi
    done
  fi
  return 1
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
  if [[ "${force_prod}" != true ]] && looks_like_production; then
    echo "'${tenant}' geldt als productie (PROD_PATTERN='${PROD_PATTERN}', PROD_TENANTS='${PROD_TENANTS}')." >&2
    echo "Voeg --force-production toe als je dit echt bedoelt." >&2
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
