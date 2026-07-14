# SPDX-License-Identifier: EUPL-1.2
# Source of truth for local + CI commands. CI calls these same targets.

PY        ?= python3
CONFIG    ?= config/woo.configuration.json
RAW       ?=
IMAGE     ?= ghcr.io/conductionnl/openwoo-provisioner:latest

.PHONY: lint sanitize test functional all help image push k8s-validate

help:
	@echo "make lint                 # gate: fail on runtime pollution / dangling refs"
	@echo "make sanitize             # strip pollution from \$$CONFIG in place"
	@echo "make sanitize RAW=x.json  # clean a fresh export x.json into \$$CONFIG"
	@echo "make test                 # run unit tests"
	@echo "make functional           # layer-2: import \$$CONFIG into an ephemeral Nextcloud (needs docker)"
	@echo "make image IMAGE=...      # build the control-plane image (webgui)"
	@echo "make push  IMAGE=...      # push the control-plane image"
	@echo "make k8s-validate         # kustomize build webgui/deploy (manifest sanity)"
	@echo "make all                  # lint + test"

lint:
	$(PY) scripts/oac.py lint $(CONFIG)

sanitize:
ifeq ($(strip $(RAW)),)
	$(PY) scripts/oac.py sanitize $(CONFIG) --in-place
else
	$(PY) scripts/oac.py sanitize $(RAW) -o $(CONFIG)
endif

test:
	$(PY) -m pytest -q tests/

functional:
	CONFIG=$(CONFIG) ./scripts/functional-test.sh

image:
	docker build -t $(IMAGE) .

# push verifieert na afloop dat de tag écht op de registry staat: drie pushes
# faalden stil (auth/rechten) terwijl de operator dacht dat ze geland waren —
# Argo rolde dan naar een niet-bestaande tag (ImagePullBackOff).
push:
	docker push $(IMAGE)
	python3 scripts/check_image_on_registry.py $(IMAGE)

# Bouw + push + registry-check in één keer; faalt luid op elke stap.
release: image push

k8s-validate:
	@if command -v kustomize >/dev/null 2>&1; then \
		kustomize build webgui/deploy >/dev/null; \
	else \
		kubectl kustomize webgui/deploy >/dev/null; \
	fi && echo "kustomize build OK"

all: lint test
