# openwoo-app-config

Versioned, **validated** OpenRegister configuration for the OpenWoo app
(Woo register, schemas, mappings, sources, synchronizations).

This repo exists for one reason: the config is a large JSON document
(~7,500 lines pretty-printed) that devs hand-edit and re-export, and
mistakes in it silently break app functionality. Here the config is
version-controlled and **gated by CI** before it is ever loaded into a
tenant. Why it works this way: [docs/design.md](docs/design.md).

## The rule

Every config change goes through this repo — never edited live in a
tenant, never imported from a hand-edited export. The flow (branch →
sanitize → lint/test → functional → PR → tag) is spelled out in
[docs/config-changes.md](docs/config-changes.md).

## What's in here

| Path | What |
|------|------|
| `config/woo.configuration.json` | The canonical, **sanitized** config (commit this) |
| `scripts/oac.py` | Linter + sanitizer — pure stdlib Python, **zero dependencies** |
| `scripts/provision.py` | Target-track provisioner **entrypoint** — thin shell over `provisionlib`, stdlib only |
| `scripts/provisionlib/` | The provisioner lib: `constants` / `helpers` / `client` / `steps` / `cli` |
| `scripts/provision_gui.py` | Optional Tkinter form front-end for `provision.py all` |
| `scripts/functional-test.sh` | Layer-2 functional test (ephemeral Nextcloud import + provision) |
| `schema/openregister-config.schema.json` | Structural envelope contract |
| `tests/` | Unit tests for linter/sanitizer and provisioner |
| `webgui/` | Hosted control-plane GUI (Flask, behind oauth2-proxy → Keycloak), incl. platform-assistent: handboek-gegronde vragen met bronvermelding, strikt lezend |
| `.woodpecker.yml` | Codeberg CI (lint + tests + secret scan) |
| `docs/` | Documentation — start at [docs/index.md](docs/index.md) |

Zero third-party dependencies is deliberate: full auditability, no
supply-chain surface, reproducible anywhere `python3` exists.

## Usage

```bash
make lint                 # CI gate: fail on runtime pollution / dangling refs
make sanitize             # strip pollution from the canonical config in place
make test                 # run unit tests
make functional           # local layer-2: import into ephemeral Nextcloud (needs docker)
```

Provisioning a real tenant (operator-driven, from outside the cluster):
[docs/provisioning.md](docs/provisioning.md); every provisioner
subcommand: [docs/provisioner-commands.md](docs/provisioner-commands.md).
