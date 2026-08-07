#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/verify-onboarding.sh — verifieert de tenant-onboarding tegen een
# draaiend cluster (sectie 6 van de change `tenant-onboarding-completion`).
#
# Automatiseert het CONTROLEREN, niet het DOEN. Een tenant aanmaken, een
# certificaat zaaien en een reveal-link versturen zijn cluster-mutaties en die
# zijn mens-vereist volgens docs/agents.md. Dit script leest alleen: het zegt
# per bewering of hij klopt, en waarom niet als hij niet klopt.
#
# De drie dry-runs delen één wegwerptenant. Maak die eerst aan via het
# formulier (Nieuwe omgeving) met een eigen frontend-host en een thema, laat
# de PR mergen, en draai dit daarna.
#
# Writes: read-only. Leest wel het admin-wachtwoord uit het tenant-secret om te
#   controleren dat het NIET in de portal-logs staat; die waarde wordt nergens
#   geprint of weggeschreven.
# Idempotent: yes (alleen leesoperaties)
# Requires: kubectl met clustertoegang, python3, jq, openssl (voor --tls-host)
#
# Usage:
#   ./scripts/verify-onboarding.sh --preflight
#   ./scripts/verify-onboarding.sh --tenant dryrun-test --theme dryrun-theme
#   ./scripts/verify-onboarding.sh --tenant dryrun-test --theme dryrun-theme \
#     --host dryrun.example.org --ingress-ip 1.2.3.4
#   IMAGE_TAG=sha-abc123 ./scripts/verify-onboarding.sh --preflight

set -euo pipefail

cd "$(dirname "$0")/.."

# Alles env-tunable; niets hardcoded dat per omgeving verschilt.
readonly PORTAL_NS="${PORTAL_NS:-openwoo-platform}"
readonly PORTAL_SA="${PORTAL_SA:-openwoo-provisioner}"
readonly PORTAL_APP="${PORTAL_APP:-openwoo-provisioner}"
# De Deployment heet openwoo-provisioner, de container erin heet `app` (naast
# de oauth2-proxy-sidecar). Die twee door elkaar halen levert een lege image-ref
# en een misleidend "onbekend" op.
readonly PORTAL_CONTAINER="${PORTAL_CONTAINER:-app}"
readonly ARGO_NS="${ARGO_NS:-argocd}"
readonly IMAGE_REPO="${IMAGE_REPO:-ghcr.io/conductionnl/openwoo-provisioner}"
# De verwachte tag komt uit de kustomization, niet uit een default hier: die
# wordt door de image-workflow bijgewerkt bij elke merge, en een tweede plek om
# hem te onderhouden is een tweede plek om hem te vergeten. Env-override blijft.
readonly KUSTOMIZATION="${KUSTOMIZATION:-webgui/deploy/kustomization.yaml}"
IMAGE_TAG="${IMAGE_TAG:-$(sed -n -E 's/^[[:space:]]*newTag:[[:space:]]*"?([^"]+)"?[[:space:]]*$/\1/p' \
  "${KUSTOMIZATION}" 2>/dev/null | head -1)}"
readonly IMAGE_TAG
readonly THEME_ENV="${THEME_ENV:-GATSBY_NL_DESIGN_THEME_CLASSNAME}"
readonly SECRET_NAME="${SECRET_NAME:-nextcloud-secrets}"
readonly SECRET_KEY="${SECRET_KEY:-nextcloud-password}"

tenant=""
theme=""
host=""
ingress_ip=""
run_preflight=false

pass_count=0
fail_count=0

ok() {
  printf '  \033[32mOK\033[0m   %s\n' "$1"
  pass_count=$((pass_count + 1))
}

bad() {
  printf '  \033[31mFOUT\033[0m %s\n' "$1" >&2
  [[ -n "${2:-}" ]] && printf '       %s\n' "$2" >&2
  fail_count=$((fail_count + 1))
}

skip() {
  printf '  --   %s\n' "$1"
}

section() {
  printf '\n== %s ==\n' "$1"
}

usage() {
  sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

# --- preflight: kan er überhaupt iets kloppen ---

check_preflight() {
  section "Preflight"

  if [[ -z "${IMAGE_TAG}" ]]; then
    bad "kan geen newTag lezen uit ${KUSTOMIZATION}" \
      "geef IMAGE_TAG=<tag> mee, of draai dit vanuit een repo-checkout"
    return
  fi

  if python3 scripts/check_image_on_registry.py "${IMAGE_REPO}:${IMAGE_TAG}" >/dev/null 2>&1; then
    ok "image ${IMAGE_TAG} staat op de registry en is anoniem pullbaar"
  else
    bad "image ${IMAGE_TAG} niet anoniem op te halen" \
      "package nog privé, of de tag is nooit gepubliceerd — zonder public heeft elke namespace een pull-secret nodig"
  fi

  local sync health
  sync="$(kubectl -n "${ARGO_NS}" get application "${PORTAL_APP}" \
    -o jsonpath='{.status.sync.status}' 2>/dev/null || echo "?")"
  health="$(kubectl -n "${ARGO_NS}" get application "${PORTAL_APP}" \
    -o jsonpath='{.status.health.status}' 2>/dev/null || echo "?")"
  if [[ "${sync}" == "Synced" && "${health}" == "Healthy" ]]; then
    ok "Argo-app ${PORTAL_APP}: Synced/Healthy"
  else
    bad "Argo-app ${PORTAL_APP}: ${sync}/${health}" \
      "kubectl -n ${ARGO_NS} get application ${PORTAL_APP} -o jsonpath='{.status.conditions[*].message}'"
  fi

  local running
  running="$(kubectl -n "${PORTAL_NS}" get deploy "${PORTAL_APP}" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="'"${PORTAL_CONTAINER}"'")].image}' 2>/dev/null || echo "")"
  if [[ "${running}" == "${IMAGE_REPO}:${IMAGE_TAG}" ]]; then
    ok "pod draait ${IMAGE_TAG}"
  else
    bad "pod draait '${running:-onbekend}', verwacht ${IMAGE_REPO}:${IMAGE_TAG}" \
      "de tag-bump in kustomization.yaml is nog niet gesynct, of het image bestaat nog niet"
  fi
}

# --- DR-1: het gedeclareerde thema haalt de frontend ---

check_branding() {
  section "DR-1 huisstijl (taak 6.1)"
  if [[ -z "${theme}" ]]; then
    skip "geen --theme opgegeven, thema-controle overgeslagen"
    return
  fi

  local got
  got="$(kubectl -n "${tenant}" get deploy -o json 2>/dev/null \
    | jq -r --arg k "${THEME_ENV}" \
      '[.items[].spec.template.spec.containers[].env[]? | select(.name==$k) | .value] | first // ""')"

  if [[ "${got}" == "${theme}" ]]; then
    ok "${THEME_ENV}=${theme} op de frontend"
  elif [[ "${got}" == "conduction-theme" ]]; then
    bad "frontend staat op de baseline conduction-theme, niet op ${theme}" \
      "tenantbestand niet gelezen, óf de frontend is ouder dan de merge — de appset ignore-difft deze env, dus alleen een VERSE frontend pikt de git-waarde op"
  else
    bad "${THEME_ENV}='${got:-leeg}', verwacht '${theme}'"
  fi
}

# --- DR-2: het tls-blok landt en cert-manager blijft eraf ---

derive_secret_name() {
  # Zelfde afleiding als webgui/tenants.py::tls_secret_name — punten worden
  # streepjes, `-tls` erachter. Bewust hier herhaald in plaats van de Python
  # aan te roepen: dit script moet ook draaien waar de repo niet staat.
  printf '%s-tls\n' "$(printf '%s' "${1,,}" | sed 's/[^a-z0-9-]/-/g; s/^-*//; s/-*$//')"
}

check_tls() {
  section "DR-2 certificaat (taak 6.2)"
  if [[ -z "${host}" ]]; then
    skip "geen --host opgegeven, certificaat-controle overgeslagen"
    return
  fi

  local want ing_json got_secret got_issuer
  want="$(derive_secret_name "${host}")"
  ing_json="$(kubectl -n "${tenant}" get ingress -o json 2>/dev/null || echo '{"items":[]}')"

  got_secret="$(printf '%s' "${ing_json}" | jq -r '[.items[].spec.tls[]?.secretName] | first // ""')"
  if [[ "${got_secret}" == "${want}" ]]; then
    ok "Ingress verwijst naar secret ${want}"
  else
    bad "Ingress-secret is '${got_secret:-geen}', verwacht '${want}'"
  fi

  got_issuer="$(printf '%s' "${ing_json}" \
    | jq -r '[.items[].metadata.annotations["cert-manager.io/cluster-issuer"]?] | map(select(.)) | first // ""')"
  if [[ -z "${got_issuer}" ]]; then
    ok "geen cert-manager-annotatie (issuer: none is doorgekomen)"
  else
    bad "cert-manager.io/cluster-issuer='${got_issuer}' staat op de Ingress" \
      "op een echte klant zou Let's Encrypt hiermee een betaald certificaat overschrijven"
  fi

  if kubectl -n "${tenant}" get secret "${want}" >/dev/null 2>&1; then
    ok "secret ${want} bestaat"
  else
    bad "secret ${want} bestaat niet" \
      "zaai hem: certswap apply k8s <bundle> --namespace ${tenant} --secret ${want}  (of kubectl create secret tls)"
  fi

  if [[ -z "${ingress_ip}" ]]; then
    skip "geen --ingress-ip, TLS-handshake niet getest"
    return
  fi
  local served
  served="$(openssl s_client -connect "${ingress_ip}:443" -servername "${host}" </dev/null 2>/dev/null \
    | openssl x509 -noout -subject 2>/dev/null || echo "")"
  if [[ "${served}" == *"${host}"* ]]; then
    ok "endpoint serveert een certificaat voor ${host}"
  else
    bad "endpoint serveert '${served:-niets}'" "verwacht een certificaat met CN/SAN ${host}"
  fi
}

# --- DR-3: rechten smal genoeg, en het wachtwoord lekt niet ---

check_reveal() {
  section "DR-3 reveal-flow (taak 6.3)"
  local sa="system:serviceaccount:${PORTAL_NS}:${PORTAL_SA}"

  if kubectl auth can-i get "secret/${SECRET_NAME}" -n "${tenant}" --as="${sa}" >/dev/null 2>&1; then
    ok "portal mag ${SECRET_NAME} lezen in ${tenant}"
  else
    bad "portal mag ${SECRET_NAME} NIET lezen in ${tenant}" \
      "rbac-secrets.yaml niet gesynct? kubectl get clusterrole ${PORTAL_SA}-tenant-secret"
  fi

  # De belangrijkste van de twee: het portaal mag niet kunnen rondkijken.
  if kubectl auth can-i list secrets -n "${tenant}" --as="${sa}" >/dev/null 2>&1; then
    bad "portal MAG secrets listen in ${tenant} — dat is te ruim" \
      "resourceNames werkt niet op list; controleer of er geen bredere ClusterRole aan deze SA hangt"
  else
    ok "portal mag GEEN secrets listen (rechten blijven smal)"
  fi

  # Log-hygiëne. De waarde wordt gelezen, nooit geprint of weggeschreven.
  local pw logs
  pw="$(kubectl -n "${tenant}" get secret "${SECRET_NAME}" \
    -o jsonpath="{.data.${SECRET_KEY}}" 2>/dev/null | base64 -d 2>/dev/null || echo "")"
  if [[ -z "${pw}" ]]; then
    skip "geen ${SECRET_KEY} in ${SECRET_NAME}; log-controle overgeslagen"
    return
  fi
  logs="$(kubectl -n "${PORTAL_NS}" logs "deploy/${PORTAL_APP}" -c "${PORTAL_CONTAINER}" --tail=2000 2>/dev/null || echo "")"
  if [[ -n "${logs}" && "${logs}" == *"${pw}"* ]]; then
    bad "het adminwachtwoord staat in de portal-logs" "dit is een incident, geen testfout"
  else
    ok "het adminwachtwoord staat niet in de portal-logs"
  fi
  if [[ "${logs}" == *"secret-link minted"* ]]; then
    ok "mint-gebeurtenis is geaudit"
  else
    skip "nog geen 'secret-link minted' in de logs (mint eerst een link)"
  fi
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --preflight) run_preflight=true; shift ;;
      --tenant) tenant="${2:-}"; shift 2 ;;
      --theme) theme="${2:-}"; shift 2 ;;
      --host) host="${2:-}"; shift 2 ;;
      --ingress-ip) ingress_ip="${2:-}"; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) echo "onbekende optie: $1" >&2; usage >&2; return 2 ;;
    esac
  done

  if [[ "${run_preflight}" == false && -z "${tenant}" ]]; then
    echo "geef --preflight en/of --tenant <naam>" >&2
    usage >&2
    return 2
  fi

  [[ "${run_preflight}" == true ]] && check_preflight

  if [[ -n "${tenant}" ]]; then
    if ! kubectl get namespace "${tenant}" >/dev/null 2>&1; then
      echo "namespace '${tenant}' bestaat niet — is de tenant-PR al gemerged?" >&2
      return 1
    fi
    check_branding
    check_tls
    check_reveal
  fi

  printf '\n%s geslaagd, %s gefaald\n' "${pass_count}" "${fail_count}"
  [[ "${fail_count}" -eq 0 ]]
}

main "$@"
