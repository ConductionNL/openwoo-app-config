## 1. Pure helpers and rendering (`webgui/tenants.py`)

- [x] 1.1 `image_version(tag)` → comparable version tuple or `None`; `compare_versions(a, b)` → `-1|0|1`. Pure, stdlib `re` only.
- [x] 1.2 `RENDERED_TOP_KEYS` → `{"tenant", "image"}`, so `unknown_keys()` stops flagging an override and `_declaration()` stops forcing read-only.
- [x] 1.3 `render()`: emit a top-level `image:` block after the `tenant:` block, only when supplied. Mirror the frontend image-pin branch (registry / repository / tag, `_q()` quoting).
- [x] 1.4 `from_declaration()`: read `doc.get("image")` into `nc_image_{registry,repository,tag}`.
- [x] 1.5 `validate()`: reuse `_TAG_RE` / `_REPOSITORY_RE` / `_REGISTRY_RE` via one shared helper for both image blocks, plus the Nextcloud-only rule that the tag must carry a version (`image_version()` not `None`). Error text names the offending value.
- [x] 1.6 `tests/test_tenants.py`: round-trip `render()` → parse → `from_declaration()`; `unknown_keys()` silent on `image`; floating tags rejected; `compare_versions("32.0.6-fpm-soap", "32.0.13-fpm")` — the case a string compare gets backwards; equal-version-different-suffix allowed.

## 2. Reads (`webgui/argolib.py`, `webgui/gitlib.py`)

- [x] 2.1 `_summary()` also returns `images` from `status.summary.images`. No extra call, no RBAC change — assert both in the test.
- [x] 2.2 `gitlib.file_history(path, limit=1)` over `GET /repos/{repo}/commits?path=…`, `GitlibError` on failure like its siblings.
- [x] 2.3 `tests/test_argolib.py` + `tests/test_gitlib.py`: fixtured responses, no network.

## 3. The guard (`webgui/server.py`)

- [x] 3.1 Effective current tag: `image.tag` from the tenant file if present, else from `values/common.yaml` via `gitlib.get_file`.
- [x] 3.2 Layer 1 — block a lower proposed version, naming both versions in the error.
- [x] 3.3 Layer 2 — compare against the Nextcloud image in `argolib`'s `images` (select by repository, not position). Block a lower version; a git/Argo disagreement is a warning.
- [x] 3.4 Layer 3 — `file_history` on a path that does not exist now ⇒ warn, naming the removed file's `dbType` and `image`.
- [x] 3.5 Wire into `_tenant_write()` after `tenants.validate()`, before the PR. Blocks as `{"errors": [...]}, 400`; new `warnings` key in the success payload.
- [x] 3.6 A failed git/Argo read refuses instead of passing — same posture as the existing `_declaration()` failure path.
- [x] 3.7 `tests/test_webgui.py`: downgrade blocked; equal/higher allowed; re-add warns with the old `dbType`; unreachable read refuses. Mock as `test_webgui.py` already does for `add_labels`.

## 4. Form (`webgui/templates/`)

- [x] 4.1 `tenant.html` + `edit.html`: collapsed "Afwijkende Nextcloud-image" section, three fields, the three rules and their reason beside them. No `digest:` field.
- [x] 4.2 Render `warnings` distinctly from `errors` — a warning must not read as a failure.

## 5. Docs

- [x] 5.1 This repo: `docs/design.md` + `CHANGELOG.md`. (Er is geen
  `webgui/README.md`; de plek voor dit soort ontwerpuitleg is `docs/design.md`.)
- [x] 5.2 **Nextcloud-base `docs/ADDING-TENANT.md`** — the section *When to use the manual route instead* (added in local commit `78b834c`, not yet pushed) says image overrides deliberately will **not** come to the portal. That is now wrong: the manual route becomes the fallback for what the portal cannot yet express. Same for `CHANGELOG.md` entry 2026-08-26 item 4.
- [x] 5.3 Bump `last_reviewed` on every page touched.

## 6. Verification

- [x] 6.1 `./scripts/verify.sh` green — the 311 existing tests included.
- [ ] 6.2 `check-test-isolation.sh` — **kon niet draaien**: pytest staat user-level
  geinstalleerd en resolveert zijn packages via `$HOME`, dus met een omgelegde
  `$HOME` faalt de import. De gate meldde "isolatie zelf was in orde", maar de
  suite liep niet. De nieuwe tests gebruiken alleen `monkeypatch` en schrijven
  niets. Bruikbaar maken vraagt pytest in een project-venv.
- [x] 6.3 Round-trip van de inhoud van het echte tenantbestand door
  `from_declaration()` → `render()`. **Semantisch** vergeleken, niet
  byte-identiek: het bestand draagt commentaarregels (`# BCT Woo versions`) en
  `render()` emit die niet, dus byte-gelijkheid is onhaalbaar en ook niet de
  eigenschap die telt. De test controleert dat geen sleutel of waarde verdwijnt,
  en dat een tweede ronde hetzelfde oplevert — geen drift bij herhaald opslaan.
- [x] 6.4 Een tenant met override én pins openen. Gedekt via de Flask-testclient
  op twee niveaus: `/declaration` geeft `unknown: []` en `editable: true`, en de
  bewerkpagina rendert de velden mét `value=` en zónder de read-only-melding.
  Nulmeting: de live portal meldde op 2026-08-26 op ditzelfde bestand nog
  *"met de hand aangepast … : image"*.
- [ ] 6.5 Browsercheck ná deploy — dat de ingeklapte secties opengaan bij een
  tenant die pins/override heeft, en dat een waarschuwing als waarschuwing leest
  en niet als fout. Kan pas na `make image` + rollout: de draaiende portal heeft
  deze code nog niet, dus een browsertest nú herhaalt alleen het oude gedrag.

## 7. Per-app versie-pins (toegevoegd 2026-08-26, zelfde change)

- [x] 7.1 `PINNABLE_APPS` als gesloten set (de drie die de ApplicationSet mapt) + `_APP_VERSION_RE` gespiegeld op `validate_app_versions_format()`.
- [x] 7.2 `RENDERED_APPS_KEYS` gains `versions`; `render()` emit het blok alleen als er iets gepind is; `from_declaration()` en `from_org()` lezen/schrijven `version_<app>`.
- [x] 7.3 `_validate_app_versions()` — aparte melding voor een leidende `v`, drie delen verplicht.
- [x] 7.4 Velden in `tenant.html` (ingeklapt) en `edit.html`; sectie klapt alleen open als de tenant pins heeft.
- [x] 7.5 Tests: render/omit, leidende `v`, onvolledige en zwevende versies, de suffix-vormen die de shell-validator accepteert, en de semantische round-trip van een bestand met `image:` **en** pins.
- [x] 7.6 `docs/design.md` + `CHANGELOG.md`.
