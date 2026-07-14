# Changelog

All notable changes to this repository are documented here.

## [Unreleased]

### Gewijzigd — 2026-07-14 (veegronde assistent — 0.3.3)
- Heartbeat in de NDJSON-stream: direct een `start`-event bij openen en
  een `ping` elke `ASSISTANT_HEARTBEAT_SECONDS` (default 10) zolang de
  sessie stil is — robuust tegen élke tussenliggende proxy-timeout
  (de 30s-breuk van gisteren kan structureel niet meer). UI negeert
  onbekende event-types al.
- `is_error` klopt nu ook bij auth-fouten: een resultaat zonder delta's
  dat als fout leest (bv. "API Error: 401") wordt een error-event én
  `is_error: true` in de audit; SDK-excepties in de worker idem
  (die misten de sleutel überhaupt).
- Pre-existing testfailure gefixt: `test_provision_in_cluster_targets_
  internal_service` (uit 242a9d8) consumeerde de gestreamde response
  nooit, waardoor de gemockte Popen nooit instantieerde — testfout, de
  productiecode was correct. Suites: 150 (systeem) / 184 (venv) groen.
- `webgui/deploy/egress-debug-runbook.md`: stap-voor-stap experiment om
  de Calico/Gardener-DNS-breuk te isoleren (drie hypotheses, beslistabel,
  rollback) — voorwaarde vóór heractivering van de egress-policy;
  verwijzing in de policy-kop, incl. notitie dat fase 2 (Prometheus)
  t.z.t. 9090/TCP nodig heeft.

### Toegevoegd — 2026-07-14 (make push verifieert de registry — stille push-fouten defused)
- `scripts/check_image_on_registry.py` + aanroep in `make push` (en nieuw
  target `make release` = image + push + check): drie image-pushes faalden
  vandaag stil op auth/rechten terwijl ze geslaagd leken, waarna Argo naar
  een niet-bestaande tag rolde (ImagePullBackOff). De check vraagt de tag
  na de push anoniem op bij Docker Hub en faalt hard als hij ontbreekt.

### Toegevoegd — 2026-07-14 (platform_status: live Argo-status voor de assistent — 0.3.2)
- Nieuwe read-tool `platform_status` (change add-assistant-live-status
  fase 1, GO Mark 2026-07-14): sync/health van alle Argo Applications via
  de RBAC die de pod al had — nul nieuwe permissies. Drie vaste weergaven
  (samenvatting/degraded/alles), vrije input geweigerd; eigen in-process
  MCP-server `platform` naast `handboek` zodat allowlist en herkomst
  gescheiden blijven. Systeemprompt: live antwoorden expliciet labelen
  (bron + tijdstip), backend weg = eerlijk zeggen.
- Audit: `status_calls`-veld per sessie, en het record wordt nu in een
  `finally` geschreven — vóór deze fix verdween het bij een
  client-disconnect (veegronde-punt).
- 6 nieuwe unit tests (149 totaal, 1 pre-existing failure in
  test_webgui provision-in-cluster, los van dit werk); docs mee:
  `docs/agents.md` (vier read-tools), deploy-README.

### Gewijzigd — 2026-07-14 (oauth2-proxy upstream_timeout 30s → 300s — assistent-502 opgelost)
- Elke assistent-vraag die >30s duurde stierf met een 502: oauth2-proxy's
  default upstream-timeout, exact 30.0s in de access-log. De stream zwijgt
  tijdens de tool-fase; /provision had nergens last van (stuurt direct
  bytes). ConfigMap-wijziging, kustomize-hash rolt de pod.
- Genoteerd voor een 0.3.2-veegronde (proefdraaimaand): heartbeat-event bij
  stream-start + periodiek (robuust tegen élke tussenliggende timeout),
  audit-record in `finally` (mist nu bij client-disconnect), en auth-fouten
  als `is_error: true` in de audit (401 logde als gewoon antwoord).

### Gewijzigd — 2026-07-14 (vraaglengte-cap in productie naar 8000)
- `deployment.yaml`: `ASSISTANT_MAX_QUESTION_CHARS=8000` — de default van
  2000 bleek in gebruik te krap. Env-only wijziging, geen image-rebuild.

### Gewijzigd — 2026-07-14 (vraaglengte-cap env-tunable; egress-rollback gedocumenteerd)
- `ASSISTANT_MAX_QUESTION_CHARS` (default 2000) vervangt de hardcoded cap in
  `webgui/assistant.py` — regel van Mark: élke limiet env-tunable, niets
  hardcoded. Zit pas in het image vanaf de volgende build (0.3.1).
- SDK-isolatie in `assistant.py` (`setting_sources=[]` + neutrale cwd):
  sessies laden geen filesystem-settings of project-`.mcp.json` meer. Dit
  verklaarde en verhielp sonnets 3× "geen toestemming" uit de benchmark
  (tweede handboek-server `conduction-docs` zichtbaar bij runs vanuit de
  repo-root); herrun van exact die drie vragen: 3/3 mét bronnen. Gaat mee
  in image 0.3.1 (`newTag` gebumpt; image éérst bouwen/pushen).
- Assistent live op platform.commonground.nu (0.3.0, Argo Synced/Healthy);
  livecheck spec-scenario's door Mark uitgevoerd. Modelkeuze
  (`ASSISTANT_MODEL`) volgt nadat het team de benchmark-testset zelf over
  de modellen heeft gedraaid (agent-run 2026-07-13: default 9/9, haiku 9/9
  en 2× sneller, sonnet 6/9 door intermitterende MCP-permissieweigering —
  rapport in /tmp/assistant-bench/).
- Egress-policy teruggetrokken na DNS-breuk op prod (bevindingen en
  vervolg-experiment in de kop van `networkpolicy-egress.yaml`).

### Gewijzigd — 2026-07-13 (incident: secret.example.yaml gedefused — geen applybare manifests meer)
- Een apply van (een kopie van) het oude example-bestand — drie complete
  Secret-manifests met placeholders — overschreef twee werkende secrets
  (oauth cookie-secret, Forgejo-token) en brak de 0.3.0-rollout en de
  tenant-PR-flow. Herstel: originele waarden teruggepatcht uit de env van
  de nog draaiende oude pod (waarden nergens getoond).
- Het bestand is nu volledig commentaar (0 YAML-documents): `kubectl apply`
  faalt hard i.p.v. stil te vergiftigen; per secret staat het juiste
  create/patch-commando erin. Regel: secrets per stuk, nooit gebundeld.

### Toegevoegd — 2026-07-13 (assistent-deploy voorbereid: taken 3.2 + 3.3 van add-platform-assistant)
- `Dockerfile`: git + ca-certificates in het image (runtime-deps van de
  handboek-contentlaag); hub gepind op sha `27cc04e8…` met build-verificatie
  (build faalt als hub-main verder is — bump is een bewuste act); env-defaults
  `HUB_DIR`/`DOCS_MCP_CACHE`.
- `webgui/deploy/deployment.yaml`: assistent-env (`ANTHROPIC_API_KEY` +
  `DOCS_READ_TOKEN` uit secret `openwoo-assistant`, beide optional zodat de
  webgui zonder assistent-secret blijft draaien), emptyDir-volumes voor
  docs-cache en `$HOME` (claude-CLI-state), memory-limit 512Mi → 1Gi.
- `webgui/deploy/networkpolicy-egress.yaml` (taak 3.2): egress-versmalling als
  apart, onafhankelijk terugdraaibaar object — DNS naar kube-system, extern
  443-only, kube-API 6443, in-cluster HTTP 80/8080. Hostnaam-pinnen kan niet
  in vanilla Calico (risico-analyse + gefaseerde testchecklist in de file-kop;
  eerdere DNS-breuk en de 0.2.7 in-cluster-provisioning meegenomen).
- `webgui/deploy/secret.example.yaml`: template `openwoo-assistant`;
  `kustomization.yaml`: newTag 0.3.0 (image éérst bouwen/pushen, dan mergen);
  deploy-README bijgewerkt (assistent-sectie, prereq 5, egress-verify).
- Apply/build/push blijft mensenwerk (cataloog: webgui deployen = mens).

### Gewijzigd — 2026-07-13 (eigenaarschap → info@conduction.nl, review WP8)
- Alle `owner:`-front-matter en CODEOWNERS omgezet van `mark` naar
  `info@conduction.nl` (opvolging na 2026-08-31). Voorbereid op branch
  `chore/wp8-ownership`; review, merge en push door een mens.

### Added — 2026-07-13 (webgui + provisioner: in-cluster mode, DNS-flap-proof)
- **Probleem**: het inrichten van een tenant via de hosted GUI faalde intermitterend
  met `[Errno -5] No address associated with hostname`. Oorzaak: de publieke
  `*.commonground.nu`-record wordt door external-dns met TTL=1 gepubliceerd en flapt
  terwijl de tenant-ingress (her)aangemaakt wordt — niets cachet een goed antwoord,
  dus elke van de 11 stappen doet een verse lookup en één "afwezig" moment breekt de
  run af (telkens op een andere stap). Geen stale cache — juist de *afwezigheid* van
  caching + een flappende record.
- **`scripts/provision_gui.py`** — nieuwe pure helper `incluster_target(base)` leidt uit
  het publieke tenant-host de cluster-lokale Service af
  (`http://nextcloud.<org>-<env>.svc.cluster.local:8080`) + het publieke host als
  `Host`-header. `build_command` gebruikt die bij optie `in_cluster` (valt terug op de
  publieke base voor niet-tenant hosts). Hergebruikt de bestaande `--host-header` van
  `provision.py` (`scripts/provisionlib/cli.py`, `client.py`).
- **`webgui/server.py`** — `/provision` leest de nieuwe checkbox `in_cluster`; audit-log
  toont de **publieke** base + `in_cluster=<bool>` (nooit de interne svc-URL).
- **`webgui/templates/index.html`** — checkbox "Via in-cluster service inrichten"
  (default aan; omzeilt de publieke DNS én een hairpin naar de eigen ingress-IP).
- **`scripts/provisionlib/client.py`** — DNS-fouten (`socket.gaierror`) krijgen een
  bruikbare melding ("kan host niet resolven … gebruik in-cluster mode of wacht 1-2 min")
  i.p.v. het rauwe errno.
- **Tests**: `tests/test_provision_gui.py` (derivatie prod/env, fallback, argv-rewrite),
  `tests/test_webgui.py` (checkbox threadt door naar interne base + `--host-header`),
  `tests/test_provision.py` (`_urlerror_detail`). `./scripts/verify.sh` groen.
- **Deploy**: de GUI-image bakt scripts, dus vergt een image-bump (`0.1.x`) — build,
  push en Argo-sync doet een mens.

### Added — 2026-07-10 (webgui: platform-assistent, v1 strikt lezend)
- **`webgui/assistant.py`** — server-side agent-sessies (Claude Agent SDK) die vragen
  beantwoorden gegrond in het technisch handboek, met verplichte herkomst (component,
  pagina, owner, last_reviewed). De sessie krijgt uitsluitend drie read-tools om de
  hub-contentlaag (`docs_mcp` als library, zelfde importlijst/max-age als het handboek)
  en geen enkele ingebouwde tool — er bestaat niets om uit te voeren of te schrijven.
  Grenzen: rate limit per SSO-identiteit (10/uur), turn-cap (12), timeout (180s),
  vraaglengte-cap; JSONL-audit van wie/vraag/antwoord/bronnen (`ASSISTANT_AUDIT_LOG`).
- **`webgui/server.py`** — `GET /assistant` (chatvenster) + `POST /api/assistant/ask`
  (NDJSON-stream: delta*, sources, done|error); zit achter de bestaande fail-closed
  SSO-gate. **`templates/assistant.html`** nieuw; **`templates/home.html`** kreeg de
  card "Vraag het platform".
- **Model-auth**: de SDK leest `ANTHROPIC_API_KEY` (default, straks ESO-secret) of
  `CLAUDE_CODE_OAUTH_TOKEN` (testfase-afwijking, besluit 2026-07-10) uit de omgeving.
- **Tests**: `tests/test_assistant.py` (limieten, audit, strikt-lezende tool-surface,
  tool-implementaties tegen een fake store; draait zonder Flask/SDK) +
  `tests/test_assistant_routes.py` (NDJSON, 429, 403 fail-closed). 147 tests groen.
- **`webgui/requirements.txt`**: + `claude-agent-sdk`, `PyYAML`.
- Spec: `platform-assistant` (techbook, change add-platform-assistant). Nog open:
  egress + Argo-deploy (taken 3.2/3.3) en de livegang-verificatie (4.x).

### Changed — 2026-06-22 (webgui: visible logout/login)
- **`webgui/deploy/oauth2-proxy.cfg`**: `skip_provider_button` `true` → **`false`**. oauth2-proxy
  now shows its own sign-in page instead of silently auto-redirecting to Keycloak, so a
  logout is *visible* (user sees they are signed out and re-authenticates explicitly) —
  the boring/auditable behaviour. Config is a hashed `configMapGenerator`, so this rolls
  the oauth2-proxy pod on next sync; **no image bump**.
- **Cross-repo dependency** (KeyCloak repo, `feature/openwoo-provisioner-sso`): the
  `openwoo-provisioner` client gains `post.logout.redirect.uris` =
  `https://platform.commonground.nu/` (and no-slash variant). Without it Keycloak rejects
  the RP-initiated `post_logout_redirect_uri` and the user is not returned to the platform.

### Changed — 2026-06-22 (webgui: Dutch + PO-jargon UI)
- All webgui pages are now **Dutch** and use **PO-jargon** (no `provision.py`/`Argo`/`tenant`
  jargon): "omgevingen", "aanvragen", friendly status (Live / Bezig… / Aandacht nodig),
  acceptatie/productie. The landing **dashboard shows open requests (aanvragen) ABOVE the
  environments** table. Card labels, forms, previews and result messages all translated.
  **Image `0.2.5`→`0.2.6`.** 144 tests pass.

### Added — 2026-06-22 (webgui: tenant creation via PR — implementation)
- **`webgui/gitlib.py`** — stdlib-only (`urllib`) Forgejo REST client: `create_branch`
  → `put_file` → `open_pr`, plus `propose_file` orchestrating all three and returning
  `{number, html_url}`. `GitlibError(status, detail)` maps cleanly to HTTP responses;
  the token is read from env and never logged or leaked into error messages.
- **`webgui/tenants.py`** — render `tenant-<name>.yaml` from form values + `validate()`
  mirroring Nextcloud-base `validate-values.sh` (name `<org>-<env>`, env-matches-suffix,
  dbType enum, apps). No PyYAML — emitted as text, zero-dep preserved. Emits
  `tenant.secrets.managed: true` so new (web-created) tenants get ESO-generated
  in-cluster secrets (gates `charts/tenant-secret` in Nextcloud-base).
- **`webgui/server.py`** — `GET/POST /tenant`: validate → render → open a PR as the
  token's identity, stamping `requested-by: <oauth2-proxy email>`; returns the PR link.
  Auth-gated by the existing fail-closed `_require_operator`. Existing `/provision`
  route and CI untouched (feature is additive).
- **`webgui/templates/tenant.html`** — the create-tenant form; shows the opened PR link
  on success, validation/conflict errors otherwise.
- **Tests**: `tests/test_gitlib.py`, `tests/test_tenants.py`, and `/tenant` cases added to
  `tests/test_webgui.py` — all offline (mocked `urlopen`, no network). Full suite: 116 passed.
- **`webgui/deploy/secret.example.yaml`** — documents the `openwoo-provisioner-git` token
  Secret + the `FORGEJO_*`/`TENANTS_*` env the webgui reads (real token out-of-band, never git).
- **`webgui/deploy/deployment.yaml`** — wires `FORGEJO_TOKEN` (from `openwoo-provisioner-git`)
  + `FORGEJO_API_URL`/`TENANTS_REPO`/`TENANTS_BASE` into the app container; oauth2-proxy stays
  the sole ingress. **Image `0.1.4`→`0.2.0`** (kustomization), built OK. Push image + create the
  token Secret out-of-band to go live.
- **`webgui/templates/home.html` + landing page** — `/` is now a landing page with use-case
  cards (**Create a tenant** → `/tenant`, **Provision config** → `/provision-config`), the
  signed-in operator, and a **Log out** link (`/oauth2/sign_out`). The original provisioning
  form moved from `/` to `/provision-config`; both sub-pages get a Home/Log-out nav. **Image
  `0.2.0`→`0.2.1`.** Tests updated (117 pass).
- **tenant form minimised to org + environment** (everything else derived) + **PR-status polling**.
  `tenants.from_org`/`validate_org`/`org_display`: operator types only the bare org + env; name
  (`<org>-<env>`), all 3 apps, db=postgres, branding `Gemeente <Org>`, and ESO-managed secrets are
  derived (advanced overrides optional). Live preview shows the derived name/host/branding as you
  type. `gitlib.get_pr` + `GET /tenant/pr-status` + result polling: the form shows the opened PR,
  then **open → merged**, then hands off to **Provision config**. **Image `0.2.1`→`0.2.2`.** 124 tests pass.
- **post-merge Argo rollout check** (`webgui/argolib.py` + `GET /tenant/argo-status`). After the PR
  merges, the form polls the Argo Application `nc-<tenant>` and shows **Synced / Healthy** before
  surfacing the **Provision config →** hand-off (so the button means "the tenant is actually up").
  Stdlib-only reader via the in-cluster API; new **cluster-scoped read-only RBAC** on argoproj.io
  Applications (`deploy/rbac-argo.yaml`) bound to the portal SA, with `automountServiceAccountToken: true`
  (least privilege — get/list/watch Applications only, no write). **Image `0.2.2`→`0.2.3`.** 130 tests pass.
- **landing dashboard** (`GET /dashboard.json` + `argolib.list_apps` + `gitlib.list_prs`). The home page
  now shows a live overview: **Tenants** (every `nc-*` Argo app with sync/health badges) and **Recent
  tenant PRs** (open/merged, linked). Stateless — derived from Argo + Forgejo, each source failing
  independently so the page always loads. So the overview persists when you navigate back Home.
- **real logout** — `GET /logout` does RP-initiated logout: clears the oauth2-proxy session **and**
  redirects to Keycloak's end-session endpoint, so `skip_provider_button` no longer silently re-logs-in.
  `oauth2-proxy.cfg` gains `whitelist_domains=["iam.commonground.nu"]`; all Log-out links point to
  `/logout`. **Keycloak `openwoo-provisioner` client must list `https://platform.commonground.nu/` as a
  valid post-logout redirect URI.** **Image `0.2.3`→`0.2.4`.** 135 tests pass.
- **batch create + delete tenant** (`gitlib.propose_files` / `get_file_sha` / `propose_deletion` +
  `GET/POST /tenant/batch` + `GET/POST /tenant/delete` + templates + landing cards + per-row delete
  links). Batch: one org per line → **one PR** adding all tenant files. Delete: a PR that **removes**
  the tenant file (Forgejo contents-delete by sha) — the PR body flags that **PV/PVCs and the
  `<tenant>-reactfront` app are NOT auto-removed** (manual cleanup, human-reviewed). **Image
  `0.2.4`→`0.2.5`.** 144 tests pass.

### Added — 2026-06-22 (openspec: tenant creation via PR)
- **`tenant-creation-pr-flow` OpenSpec change proposal** (first openspec in this repo).
  Extend the SSO-gated webgui so an operator creates a WOO tenant from a form: it renders
  `tenant-<name>.yaml`, validates it, and opens a PR on Nextcloud-base via the Forgejo REST
  API using **stdlib `urllib`** (zero-dep posture preserved), returning the PR link to the
  form. Boring/auditable boundary: **portal = PR-author only** — no cluster write, no secrets
  (tenant secrets generated in-cluster via **ESO**), merge stays a human review gate, and the
  git-write identity (a scoped Codeberg bot) is decoupled from the operator's SSO login.
  Proposal/design/tasks only; no webgui code yet. See `openspec/changes/tenant-creation-pr-flow/`.

### Changed — 2026-06-15 (control-plane image 0.1.4)
- Built + tagged control-plane image **`docker.io/conduction2022/openwoo-provisioner:0.1.4`**
  (carries the `provisionlib` refactor + the `delete-menu` step). Bumped
  `webgui/deploy/kustomization.yaml` and `deployment.yaml` from `0.1.3` → `0.1.4`.
  The previous `0.1.3` image is untouched in the registry (rollback target).
- Added **`.dockerignore`**: the image build had no ignore file, so `COPY webgui/`
  would bake the 34 MB host dev venv (`webgui/.venv`) and `__pycache__` into the
  image. The venv is rebuilt from `webgui/requirements.txt` inside the image, so
  the host copy is excluded — smaller, reproducible, no stray host artefacts.

### Changed — 2026-06-15 (modularise the provisioner into a lib)
- Split the 1479-line `scripts/provision.py` monolith into the **`provisionlib`**
  package, each module small and separately auditable:
  - `constants.py` — every API path + provisioning default (pure literals)
  - `helpers.py` — pure, unit-tested helpers + `log` (no live stack)
  - `client.py` — the basic-auth JSON `Client` + `ProvisionError`
  - `steps.py` — the `provision_*` domain logic + `provision_all` orchestrator
  - `cli.py` — argparse wiring, secret resolution, `cmd_*` dispatch, `main`
- `scripts/provision.py` is now a **thin entrypoint** (delegates to
  `provisionlib.cli.main`); it inserts its own dir on `sys.path` so the GUI,
  webgui and `functional-test.sh` keep shelling out by path unchanged. The CLI
  surface (14 subcommands) is byte-for-byte the same — function bodies were moved
  verbatim, not rewritten.
- Callers can now `import provisionlib as provision` to reuse steps as a library;
  the package `__init__` re-exports the public surface.
- Tests: `tests/test_provision.py` now imports `provisionlib`; orchestrator tests
  patch `provision.steps.*` (the namespace `provision_all` resolves). No
  behavioural change — 84 passed, 1 skipped, same as before the split.

### Added — 2026-06-15 (delete-menu: remove OpenCatalogi default User Menu)
- **`provision.py delete-menu`** (+ step `[6/11]` in `all`): OpenCatalogi
  auto-creates a default **`User Menu`** object on the `publication` register; it
  does not belong in the WOO config (no per-tenant menu is shipped), so the
  provisioner removes it. GETs `publication/menu`, matches `User Menu`
  case-insensitively against each object's `name`/`title`/`slug`, DELETEs every
  match by `uuid` (falls back to `id`), then re-GETs the list and asserts the
  match is gone. **Idempotent**: skips with a log when absent. Override the match
  with `--menu-name`; skip the step in `all` with `--skip-delete-menu`.
- `Client.delete()` added (thin DELETE wrapper, mirrors `get`/`post`/`put`).
- Step numbering in `all` renumbered `/10` → `/11` (delete-menu inserted after
  `catalog`, before `credentials`).
- Tests: `_menu_matches` (name/title/slug, case-insensitive) + `provision_delete_menu`
  (delete by uuid, fallback to id, idempotent-when-absent, raises-if-still-present);
  orchestrator order test updated. 64 passed.

### Added — 2026-06-12 (rule source resolver + OpenRegister settings hardening)
- **`provision.py rules`** (+ step in `all`'s `sync-refs`): resolve each
  `fetch_file` rule's `configuration.fetch_file.source` slug → tenant numeric
  source id. Same forward-reference gap as the syncs (the import leaves it a slug
  or drops it on a fresh tenant, so the fetch_file action can't resolve the
  source). Idempotent. Confirmed live on canary 1.1.1 (`fetch_file.source=1 OK`).
  `BUG-import-forward-refs.md` extended to cover this case.
- **`provision_settings`** now also applies (idempotent, partial PUT merges):
  - `PUT /settings/retention` → **audit + search trails OFF**
    (`auditTrailsEnabled`/`searchTrailsEnabled` = false) — WOO syncs create many
    objects; the trails add overhead with little value here. Governance trade-off.
  - `PUT /settings/files` → **text extraction = `manual`** (`extractionMode`).
    Best-effort: the endpoint is OpenRegister 1.1.x+, skipped with a log on older
    tenants. Confirmed live on canary 1.1.1.
- **Not done — object text extraction (`objectExtractionMode` = manual):** lives in
  the `objectManagement` app value and is NOT settable via the settings API (the
  `/settings/objects` write endpoints are vectorization-only / return 405). Set it
  in the OpenRegister UI per tenant, or via `occ config:app:set`.
- Tests: 77 passed, 1 skipped (rule resolver ×3, unwrapped-settings ×1).

### Fixed — 2026-06-10 (btree index overflow on array fields)
- `attachments` and `values` (both `array`) were `facetable: true` in all 17
  schemas, so OpenRegister/MagicMapper put a **btree index** on the serialised
  array. A large attachments list overflowed Postgres' index-row limit on sync:
  `SQLSTATE[54000] index row requires 59520 bytes, maximum size is 8191`
  (gooisemeren.migrate). Faceting on a serialised array is meaningless anyway.
- Fix: `facetable: false` on `attachments` + `values` (34 properties, both arrays).
  `thema`/`titel` stay facetable (short, sensible facets). Lint 0/0.
- Note: like a column-type change, an existing index is not dropped in place —
  a tenant whose table already has the index needs a fresh table (re-wipe) for
  the fix to take effect.

### Added — 2026-06-10 (resolve synchronization slug references — fresh-tenant fix)
- New `provision.py syncs` step (and step `[7/10]` in `all`): after import, resolve
  each synchronization's slug references — `sourceId`, `sourceTargetMapping`,
  `actions` (rule slugs), `targetId` (`register/schema`) — to the tenant's numeric
  ids and PUT them, asserting they reflect. Idempotent (no-op once resolved).
- Why: the OpenRegister/OpenConnector import only resolves cross-object references
  against what already exists in the *same* pass, so on a **fresh** tenant a sync's
  forward references stay slugs and break at run time — e.g.
  `SQLSTATE[22P02] invalid input syntax for type bigint: "demo-xxllnc"` (sourceId
  still the source slug). Re-importing only shifts the problem (resolves syncs but
  re-slugs the jobs). This mirrors how `provision_jobs` already resolves a job's
  `synchronizationId`, so one import + the two resolve steps converge a clean tenant.
- Tests: 4 new (resolve all refs / idempotent / unknown-source / reflect-assert).
  73 passed, 1 skipped.

### Fixed — 2026-06-10 (samenvatting varchar(255) overflow on sync)
- All 17 WOO schemas defined `samenvatting` as `string` with `maxLength: 255`,
  which OpenRegister's MagicMapper materialises as a `varchar(255)` column. Real
  xxllnc data (e.g. a Woo-verzoek summary) exceeds 255 chars → the sync-run failed
  with `SQLSTATE[22001] value too long for type character varying(255)` when saving
  the object (confirmed in the canary nextcloud.log, table `openregister_table_2_17`).
- Fix: `samenvatting` → `format: "text"` (drop `maxLength`), matching the existing
  convention for long free-text fields (`titel`, `categorie`) so MagicMapper creates
  a TEXT column. Applied to all 17 schemas. Lint 0/0.
- Note: an existing `varchar(255)` column is not reliably widened in place; a tenant
  whose table was already created needs a fresh table (reset) for the TEXT column to
  take effect. Broadening the other free-text fields to `text` is a follow-up
  ("optimaliseren") once the targeted fix is confirmed on a clean canary.

### Changed — 2026-06-10 (Phase 3 deploy wiring — real image + namespace hardening)
- Image pinned to `docker.io/conduction2022/openwoo-provisioner:0.1.0` (built +
  pushed; keep the Docker Hub repo **private** — the image bundles `config/` +
  `scripts/`). `deployment.yaml` + `kustomization.yaml` updated.
- `namespace.yaml`: Pod Security `restricted` labels (matches platform convention;
  the deployment's securityContext complies).
- Argo wiring lives in **Nextcloud-base** (`nextcloud-platform/argo/`): an
  `Application` (`apps/openwoo-provisioner.yaml`) deploying `webgui/deploy` from
  Codeberg into `openwoo-platform`, plus the `openwoo-platform` destination added
  to the `nextcloud-platform` AppProject.
- SSO: the Google identity provider already exists in Keycloak (configured via UI);
  the realm import omits `identityProviders` so it is reused, not overwritten.

### Added — 2026-06-09 (web control-plane — Phase 3: Kubernetes deploy)
- `Dockerfile` (repo root) — control-plane image: `python:3.12-slim` + Flask +
  **gunicorn** (gthread, `--timeout 3600` so the streaming `/provision` log isn't
  cut), running the app **bound to `127.0.0.1:8081`**, non-root, unprivileged.
- `webgui/deploy/` — kustomize bundle for `https://platform.commonground.nu`:
  - `deployment.yaml` — **one pod, two containers**: the app (localhost only) +
    an **oauth2-proxy** sidecar that is the sole network listener (`:4180`) and
    forwards the authenticated identity to the app. Hardened securityContext
    (readOnlyRootFs, drop ALL caps, runAsNonRoot, seccomp RuntimeDefault).
  - `service.yaml` (ClusterIP `80→4180`), `ingress.yaml` (nginx + `letsencrypt-prod`
    TLS; buffering off + long read-timeout for streaming).
  - `networkpolicy.yaml` — pod ingress **only** from the `ingress-nginx` namespace
    on `:4180`; egress DNS + 443. With the localhost bind this **enforces in code**
    the "oauth2-proxy is the sole ingress" trust anchor (the Phase-2 review follow-up).
  - `oauth2-proxy.cfg` (moved here from `webgui/auth/`) → hashed ConfigMap via
    `configMapGenerator`; `secret.example.yaml` is a template (real Secret
    out-of-band); `argocd-application.example.yaml` for the GitOps repo.
  - `webgui/deploy/README.md` — build/push, prerequisites, apply, verify.
- `Makefile`: `image` / `push` / `k8s-validate` targets.
- Verified: `kustomize build` renders 7 resources (ConfigMap hash + volume
  rewrite); the image **builds and runs** — in-container `/healthz`=200, `/`
  without auth=**403 fail-closed**, `/` with `X-Forwarded-Email`=200.

### Added — 2026-06-09 (web control-plane — Phase 2: auth via oauth2-proxy → Keycloak)
- The web GUI is now **fronted by oauth2-proxy → Keycloak** (realm `commonground`,
  `iam.commonground.nu`), which **brokers Google** as identity provider — operators
  log in with Google, the app integrates only with Keycloak (OIDC).
- **App fails closed**: `server.py` now enforces `REQUIRE_AUTH` (default on) — every
  route except `/healthz` returns `403` without an `X-Forwarded-Email`/`-User`
  identity header. So a request that bypasses the proxy is refused, not served.
  The header is trustworthy only because oauth2-proxy is the **sole ingress**
  (app bound to localhost / NetworkPolicy). Local dev: `REQUIRE_AUTH=false`.
- `webgui/auth/oauth2-proxy.cfg` — proxy config (Keycloak OIDC upstream→Flask;
  `pass_user_headers`; `cookie_samesite=lax` closes the cross-site POST CSRF noted
  in the Phase-1 review). Secrets (`OAUTH2_PROXY_CLIENT_SECRET` / `_COOKIE_SECRET`)
  injected from env, never in the file.
- `webgui/auth/README.md` — trust model, the Keycloak `openwoo-provisioner` client +
  Google IdP to add (KeyCloak repo), required secrets, local fail-closed smoke test.
- Tests: 3 new auth-guard tests (403 unauthenticated, 200 with header, `/healthz`
  stays open). 8 webgui tests green under the venv; 69 + 1 skipped (system python).
- **KeyCloak repo** (separate, prod-path): adds the `openwoo-provisioner` OIDC
  client + Google identity provider to `realm-commonground.yaml`.

### Added — 2026-06-09 (hosted web control-plane — Phase 1: core, no auth)
- `webgui/` — a small **Flask** app that drives provisioning from **outside** the
  cluster against a tenant's **public URL**, reusing the tested
  `provision_gui.build_command()` (so the web form becomes `provision.py all`).
  - **Creds model A**: the operator enters the tenant password + source API key
    per run; the app **stores nothing**. Secrets reach the subprocess via **env**
    (`GUI_PROVISION_PASSWORD` / `GUI_PROVISION_APIKEY`), never argv — and are
    never logged (the audit line is `user + base + options` only).
  - Routes: `GET /` (form), `GET /healthz`, `POST /provision` (streams the
    `provision.py` step log back to the browser).
  - `current_user()` reads `X-Forwarded-Email`/`-User` — wired for the **Phase 2**
    oauth2-proxy → Keycloak (Google-brokered) front; **no auth in Phase 1** by
    design (run locally / behind a trusted network).
  - Files: `webgui/server.py`, `webgui/templates/index.html`,
    `webgui/requirements.txt` (`Flask>=3,<4`). Dev venv in `webgui/.venv/`
    (git-ignored).
  - Tests: `tests/test_webgui.py` (Flask test client) — `importorskip("flask")`
    so the system-python `make test` run **skips** it; run it under the venv
    (`webgui/.venv/bin/python -m pytest tests/test_webgui.py`). Verified
    end-to-end against canary (`POST /provision` → `FULL PROVISIONING OK`).

### Decided — 2026-06-09 (provisioning is operator-driven, not in-cluster)
- Provisioning runs from **outside** the cluster against a tenant's **public URL**
  (a trusted domain) via the CLI/GUI — not as in-cluster Argo Jobs. An in-cluster
  Argo path was prototyped (kustomize ConfigMap + per-tenant PostSync Job +
  ApplicationSet) and **removed**: the internal service Host (`nextcloud:8080`)
  isn't a trusted_domain (HTTP 400), and it created standing per-tenant Argo apps
  that weren't wanted. Removed `kustomization.yaml`, `deploy/`, `argocd/`,
  `Dockerfile`, and the `make image/push` targets. `provision.py all
  --skip-credentials` and `--host-header` remain (useful for any internal run).

### Changed — 2026-06-09 (idempotent — skip writes when already converged)
- Every write step now GET-checks first and **skips the write when the tenant is
  already in the desired state**, so a re-run on a converged tenant is a near
  no-op (only GETs). Applies to `settings`, `oc-settings`, `catalog`,
  `credentials`, and `jobs` (compares the *tenant's* current value, not the
  config slug). Assertions still run after any write.
- `import` is idempotent too: it runs `verify-import` first and **skips the
  upload when every config slug is already present**. `--force` (import) /
  `--force-import` (all) / GUI "Force re-import" re-upload anyway — needed when
  the config *content* changed (a slug-level check can't see that).
- Verified on canary: a second `all` run skips all steps (`0 job(s) updated`,
  "already present, skipping", etc.). 7 new unit tests.

### Added — 2026-06-09 (job-user workaround for the Anonymous-job bug)
- The `jobs` step now **always sets each job's `userId`** — defaulting to the
  admin `--user`, overridable with `--job-user <user>` (GUI: "Job user" field,
  blank = admin). Workaround for scheduled SynchronizationAction jobs running as
  `Anonymous` and being denied object writes
  (docs/BUG-sync-job-anonymous-permission.md); effective only if the runner
  honours `userId` (unverified — a manual authenticated run works regardless, so
  only the scheduled cron run proves it). The `jobs` step no longer skips a job
  when only the userId needs setting. 3 unit tests.

### Added — 2026-06-09 (Tkinter front-end)
- `scripts/provision_gui.py` — optional Tkinter form (tenant URL, admin user, app
  password, source URL, API-Interface-ID, source API key) that runs
  `provision.py all`, passing secrets via env (never argv) and streaming output.
  A **"Run synchronizations after provisioning"** checkbox adds `--run-syncs`
  (with a dry-run `--test` sub-option) so the syncs fire as part of the run.
  Pure-stdlib; with no Tkinter/display it falls back to printing the terminal
  command. Testable `build_command` core (4 unit tests).

### Added — 2026-06-09 (per-tenant source connection params)
- `provision.py credentials` / `all` now also set the source **`location` (URL)**
  and **`API-Interface-ID`** header alongside the API key — all three are
  per-tenant (each client's source system differs) and supplied at provision time
  (`--source-url` / `--api-interface-id` or interactive prompt; blank keeps the
  config default). Not committed to the config. The Argo reconciler handles the
  base config; these per-tenant source values are operator-supplied. 2 unit tests.
- `provision.py` is now fully interactive: omitting `--user` / `--password` /
  `--apikey` / `--source-url` / `--api-interface-id` prompts for them on a
  terminal (getpass for secrets), so `provision.py all --base <tenant>` asks for
  everything. Non-terminal runs still require flags/env.

### Added — 2026-06-09 (jobs synchronizationId resolution)
- `scripts/provision.py jobs` — after import, each job's `arguments.synchronizationId`
  is still the sync **slug** (the import does not resolve it, unlike sync
  `targetId`), but the `SynchronizationAction` needs the **numeric** sync id to
  trigger. This step resolves the sync slug → the tenant's numeric sync id and
  PUTs it onto the job, asserting it reflects. The config keeps the portable
  slug; the provisioner resolves it per tenant (like `catalog`). Added to the
  `all` flow (now 9 steps, after `sync-check`). Verified on canary: all 16 jobs'
  synchronizationId became numeric ids. 4 unit tests.

### Changed — 2026-06-09 (review + refactor, pre-Argo)
- Post-`/review` + `/security-review` cleanup (security review: no findings):
  - rewrote `scripts/provision.py`'s module header to describe all 11 subcommands
    + the idempotent-convergence model (was stale, only described `credentials`);
    refreshed the `import` / `authorization` / `all` `--help` strings.
  - `Client` now warns when `--base` is plain `http://` (non-localhost) — basic-auth
    would go in cleartext. Warning only, does not block.
  - documented that the list endpoints are assumed unpaginated (verify-import /
    catalog / oc-settings) so a future paginating API gets caught in review.
  - small: `config_source_slugs` delegates to `config_slugs`; dropped a redundant
    `import sys`.

### Changed — 2026-06-09 (simplified now that the import bug is fixed)
- OpenRegister **1.0.3** imports `authorization.inheritFromPublic` natively
  (the 0.2.3 bug is fixed — verified by an A/B clean test on canary: the raw
  config imports 17/17 schemas with the flag preserved in the DB). So:
  - `provision.py import` now uploads the config **as-is** (no strip).
  - the `authorization` step is **removed from the default `all` flow** and kept
    as a **standalone repair command** (e.g. flip `inheritFromPublic=false` on an
    existing tenant for department isolation). `all` is now 8 steps.
- `provision.py all` verified end-to-end on a freshly-reset canary: 17/17 schemas
  with `inheritFromPublic=true` in the DB **without** the restore step.

### Fixed — 2026-06-09 (handle inheritFromPublic instead of dropping it)
- Root cause of the 4 silently-dropped schemas (`adviezen`, `convenanten`,
  `wetten_en_algemeen_verbindende_voorschriften`, `woo_verzoeken_en_besluiten`):
  each carries `authorization.inheritFromPublic`. OpenRegister's **import**
  rejects that key and silently drops the schema (HTTP 200, `failed:[]`),
  leaving the 4 syncs dangling. Confirmed from the canary log
  (`[ImportHandler] ... Invalid authorization action 'inheritFromPublic'`) and
  reproduced from a clean NC 32.0.5 reinstall.
- `inheritFromPublic` is a **legitimate, intended** authorization flag (default
  `true`; setting it `false` isolates publications per department, e.g. Almere),
  so it is **kept in the config**, not stripped. The schema UPDATE API *does*
  accept it — only the import does not.
- Handling (provisioner): `provision.py import` strips `inheritFromPublic` for
  the import call so every schema lands, then `provision.py authorization`
  restores it via the schema UPDATE API and asserts it reflects.
  `scripts/functional-test.sh` now imports through `provision.py import`.
- `scripts/oac.py`: **bad-authorization** lint check now allows the
  `inheritFromPublic` flag alongside the create/read/update/delete actions, and
  flags any *other* unrecognised authorization key.
- Verified A-to-Z on a clean canary: import → 17/17 schemas → authorization sets
  `inheritFromPublic=true` on the 4 (confirmed in the DB) → catalog links all 17
  → sync-check clean.

### Changed — 2026-06-08 (orchestrator)
- `scripts/provision.py all` now runs the full bring-up: settings → **oc-settings**
  → verify-import → **catalog** → credentials → sync-check → (optional sync-run).
  `--skip-oc-settings` / `--skip-catalog` for a WOO-only tenant without the
  OpenCatalogi base. Proven end-to-end on canary (all steps green).

### Added — 2026-06-08 (OpenCatalogi settings)
- `scripts/provision.py oc-settings` — couples each OpenCatalogi object type
  (catalog/listing/organization/theme/page/menu/glossary) to its register +
  schema via `POST /apps/opencatalogi/api/settings`, resolving the `publication`
  register slug and each type's same-named schema slug to tenant ids, then
  asserting the coupling reflects. Makes the coupling reproducibly owned by the
  provisioner instead of relying on the base install. Proven on canary; 3 unit
  tests. (Default-organisation is left on `auto_create_default_organisation`;
  multitenancy is set to disabled by the `settings` step.)

### Added — 2026-06-08 (catalog)
- `scripts/provision.py catalog` — points the OpenCatalogi `publications` catalog
  object at the WOO register + **all** its schemas, resolving the register and
  schema slugs to tenant ids (the object stores numeric ids) and asserting they
  reflect. 3 unit tests. Proven on canary (17 schemas linked).

### Added — 2026-06-08 (jobs)
- `config/woo.configuration.json` now carries the **16 synchronization jobs**
  (one `SynchronizationAction` per sync, `interval` 1800s) that the OpenRegister
  configuration export omits. Sourced from the toolchain reference config — the
  only structural difference between it and our config was these jobs (all other
  buckets had identical slug sets). Each job is **sanitized** (runtime fields
  stripped) and made **portable**: `arguments.synchronizationId` was rewritten
  from the instance-specific numeric id to the synchronization **slug**.
- `scripts/oac.py`: added a `jobs` runtime strip-set (`executionTime`,
  `jobListId`, `lastRun`, `nextRun`, `reference`, `status`, `userId`, `version`)
  and a reference-integrity check that every `SynchronizationAction` job's
  `synchronizationId` resolves to a synchronization slug (a numeric/unknown ref
  is non-portable and now fails lint). 3 unit tests.
- Verified against canary: the jobs-bearing config imports HTTP 200 and creates
  all 16 jobs with the slug reference preserved. (Local `make functional` was
  separately blocked by an appstore download failure for the openregister app,
  unrelated to this change.)

### Added — 2026-06-08 (full-flow orchestrator)
- `scripts/provision.py all` — runs the bring-up in order and gates on each step:
  settings → verify-import → credentials → sync-check → (optional `--run-syncs`).
  Stops at the first failed assertion. Object creation and job runs stay separate.
  Proven against canary: it correctly halts at verify-import on the partial-import
  bug (4 schemas missing) rather than proceeding. 4 unit tests for the sequencing.
- `verify-import` now also covers the `jobs` bucket (skipped while the config has
  no jobs; active once jobs land after the hotfix).

### Added — 2026-06-08 (sync-run + objects)
- `scripts/provision.py sync-run` — POST `…/api/synchronizations/{id}/run` (or
  `--test` for the `/test` dry-run) for every config synchronization, resolved
  by slug, asserting no error. A real run fetches live data from the source, so
  it targets a real tenant, not the local CI test. Endpoints confirmed from the
  OpenConnector source (`synchronizations#run` / `#test`).
- `scripts/provision.py objects` — create one object in a register/schema
  (`POST …/api/objects/{register}/{schema}`) from a JSON payload file and assert
  the response carries an id/uuid.
- 6 unit tests (run vs test endpoint, error/ missing-sync paths, object id assert).
- Live-testing of these two is deferred until after the OpenRegister import
  hotfix (the partial-import bug leaves canary's syncs dangling until then).

### Added — 2026-06-08 (settings provisioning)
- `scripts/provision.py settings` — PUT `…/settings/organisation` (relies on
  `auto_create_default_organisation` by default; `--default-organisation` to pin
  a UUID) and `…/settings/multitenancy` (disabled by default, `--multitenancy`
  to enable), then GET each back and assert the sent fields reflect. Proven
  against canary. 2 unit tests.

### Added — 2026-06-08 (tenant verification)
- `scripts/provision.py verify-import` — after an import, compares the config's
  slugs (registers/schemas/sources/synchronizations) against what is actually
  present on the tenant and fails on any missing entity. The import API returns
  HTTP 200 even when it silently drops rows, so on a tenant that already held
  data the bulk row count cannot see the gap; this slug-level diff can.
- `scripts/provision.py sync-check` — asserts every config synchronization
  resolved its target schema on the tenant (targetId rewritten to a numeric
  `register/schema` id), flagging any left dangling as `register/<slug>`.
- Both proven against canary, where they caught a real OpenRegister import bug:
  importing this config into a non-empty instance (8 pre-existing OpenCatalogi
  base schemas) silently created only 13 of 17 WOO schemas — leaving 4 schemas
  absent and their 4 synchronizations dangling — while returning HTTP 200
  "Import successful". Confirmed via the canary DB: the 4 are genuinely not
  created (not soft-deleted / org-filtered). See `docs/PROVISIONING-TEST-PLAN.md`.
- 9 more unit tests (slug diff, dangling-target detection, config-scoped filtering).

### Added — 2026-06-08 (provisioning / credentials)
- `scripts/provision.py` — post-import tenant provisioner (pure stdlib, urllib).
  `credentials` subcommand: resolves every config source by slug on the running
  instance, sets the `headers.API-KEY` entry in the source `configuration`
  (read-modify-write, preserving existing headers like `API-Interface-ID`), then
  GETs the source back and asserts the key reflected. Key comes from `--apikey`
  / `--apikey-env` (never logged); a clearly-marked dummy is used when none is
  supplied (CI / local test).
- `scripts/functional-test.sh` — new `provision_credentials` step after the
  row-count check; proves the credential-provisioning path end-to-end (dummy
  key). Verified on NC30 + openconnector 0.2.20.
- `tests/test_provision.py` — 11 unit tests (slug resolution, header merge /
  config preservation, dummy vs supplied key, missing-source and
  no-reflection failure paths).
- `config/woo.configuration.json` — source `demo-xxllnc` gains an **empty**
  `configuration."headers.API-KEY"` placeholder. The real demo key is injected
  at provision time from a secret / env var and is never committed.

### Finding — 2026-06-08
- OpenCatalogi settings / default-catalog / home-page provisioning steps operate
  on OpenCatalogi's **own** entities (a `publication` register, `catalog`/
  `listing`/… schemas) which are NOT in the WOO config — a fresh import yields
  only register `woo` + 17 WOO schemas. Those steps require a separate
  OpenCatalogi-base provisioning flow and are out of scope for this repo's WOO
  config. Details in `docs/PROVISIONING-TEST-PLAN.md`.

### Added — 2026-06-08
- Initial scaffold of the OpenWoo config validation repo.
- `scripts/oac.py` — pure-stdlib linter + sanitizer for OpenRegister
  configuration exports. Detects and strips "export-on-import-on-postgres"
  runtime pollution (sync cursors, content hashes, last-synced timestamps,
  source rate-limit counters / `lastCall` / `objectCount`, register `usage`,
  created/updated metadata). Per-bucket strip-set: `schemas[*].version` is
  preserved (semantic); behavioural flags (`published`, `deleted`, `owner`,
  `rateLimitLimit/Window`, `isEnabled`) are never auto-stripped.
- Reference-integrity checks: `synchronizations[*].sourceId` and `targetId`
  must resolve to existing sources / registers / schemas.
- `schema/openregister-config.schema.json` — structural envelope contract.
- `tests/test_oac.py` — unit tests (pollution, schema-version preservation,
  dangling refs, sanitize idempotency, data-leak warning).
- `.woodpecker.yml` — Codeberg CI: lint + tests + gitleaks secret scan.
- `docker-compose.test.yml` + `scripts/functional-test.sh` — Layer-2 functional
  test: ephemeral Nextcloud + PostgreSQL, installs the Conduction apps, imports
  the config via `POST /apps/openregister/api/configurations/import` and asserts
  success, then **verifies PostgreSQL row counts** (registers/schemas/sources/
  mappings/rules/synchronizations) against the config — the import returns
  HTTP 200 even when its response omits created rows, so the count check is the
  real proof. Auto-detects compose (docker/podman). Local-only (Codeberg runners
  provide buildah, not docker/compose); no live credentials. `make functional`.
  Verified end-to-end: a fresh NC30 + openregister 1.0.3 / openconnector 0.2.20 /
  opencatalogi 1.0.3 imports the WOO config with all 17 schemas + 16 syncs.
- README: mandatory contribution workflow — every config change goes through
  this repo (branch → sanitize → lint/test → functional → PR → tag).
- `Makefile` — source of truth for local + CI commands.
- `config/woo.configuration.json` — initial canonical config, sanitized from
  `configuration_7_2026-06-08.json` (261 runtime fields stripped).
