# CLAUDE.md

Gevalideerde OpenRegister-configuratie voor de OpenWoo-app. Lees eerst
`docs/design.md` (waarom), dan `docs/config-changes.md` (de verplichte
flow). De provisioner (`scripts/provision.py` + `provisionlib/`) is
operator-driven en idempotent — elke stap GET-checkt eerst.

## Kernregels

- Elke config-wijziging via de flow: raw export → `make sanitize` →
  `make lint && make test` → PR → tag. Nooit live in een tenant editen.
- Credentials komen uit env/secret-store, nooit uit git; de config
  draagt een lege placeholder voor de API-key.
- Zero third-party dependencies in de tooling is een bewuste keuze.

## Agent-guardrails

- Operatie-cataloog: `docs/agents.md` — **niet gecatalogiseerd = eerst
  vragen**.
- Grondwaarheid: MCP `conduction-docs` (handboek) boven modelkennis.
- Vóór afronden: `./scripts/verify.sh` groen; docs mee in dezelfde
  wijziging. Push, provisioning-runs en releases doet een mens.
  Nooit `--no-verify`.
