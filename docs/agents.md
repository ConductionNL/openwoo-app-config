---
last_reviewed: 2026-07-08
owner: mark
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

## Grondwaarheid en gedrag

- Handboek (MCP `conduction-docs`) boven modelkennis; docs/design.md
  legt de twee-sporen-architectuur uit.
- GET-check-first is hier al code: neem het provisioner-patroon over
  in elke nieuwe stap (elke step assert zijn eigen effect).
