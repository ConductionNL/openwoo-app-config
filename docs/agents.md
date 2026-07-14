---
last_reviewed: 2026-07-10
owner: info@conduction.nl
---

# Agent-cataloog (referentie)

Guardrails voor agents in deze repo, per het handboek-formaat
(org → Werken met agents). **Niet in dit cataloog = eerst vragen.**

De provisioner van deze repo is het huisvoorbeeld van idempotentie:
elke stap GET-checkt eerst en slaat over wat al klopt.

## Operaties

| Operatie | Autonomie | Idempotentie | Verificatie |
|---|---|---|---|
| Config-wijziging via de flow (export → `make sanitize` → lint/test) | autonoom t/m commit | sanitizer is idempotent; lint bewaakt | `make lint && make test` (verify); PR-flow uit docs/config-changes.md |
| Linter/sanitizer/provisioner-code wijzigen | autonoom | n.v.t. (code) | unit tests (116+) groen; nieuwe checks krijgen tests |
| Docs bijwerken | autonoom | tekstueel | docs-contract-gate |
| `make functional` (docker, layer-2) | mens-vereist | test is zelf idempotent (down -v) | lokaal, geen gate — te traag |
| `provision.py` draaien tegen een tenant | mens-vereist | provisioner is idempotent, máár: credentials + productie-tenant | operator-driven per ontwerp (docs/provisioning.md) |
| Release taggen (Nextcloud-base consumeert de tag) | mens-vereist | — | pas na groene gates |
| webgui deployen | mens-vereist | — | webgui/deploy-flow |
| Push | mens-vereist | — | gates draaien bij de mens |
| Echte API-keys/credentials in config of git | verboden | — | secret-scan in CI; config draagt lege placeholder |

## Platform-assistent (webgui, v1 strikt lezend)

De webgui bevat een assistent-endpoint (`/assistant`) dat server-side
agent-sessies draait: vragen beantwoorden, gegrond in het handboek, met
verplichte herkomst. De sessie krijgt uitsluitend vier read-tools en
geen enkele ingebouwde tool; er valt dus niets uit te voeren of te
schrijven:

- `search_docs`, `read_page`, `list_components` — de hub-contentlaag
  als library (handboek);
- `platform_status` — actuele Argo-sync/health via de RBAC die de pod
  al had (change add-assistant-live-status fase 1, GO 2026-07-14):
  alleen vaste weergaven, geen vrije input; antwoorden worden expliciet
  als live gelabeld (bron + tijdstip), gescheiden van handboek-herkomst.

Grenzen: rate limit per SSO-identiteit, turn-cap, timeout; elke sessie
wordt geauditeerd (wie/vraag/antwoord/bronnen/status-aanroepen).
Zie `webgui/assistant.py` en de spec `platform-assistant` in techbook.

## Grondwaarheid en gedrag

- Handboek (MCP `conduction-docs`) boven modelkennis; docs/design.md
  legt de twee-sporen-architectuur uit.
- GET-check-first is hier al code: neem het provisioner-patroon over
  in elke nieuwe stap (elke step assert zijn eigen effect).
