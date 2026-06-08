# SPDX-License-Identifier: EUPL-1.2
# Source of truth for local + CI commands. CI calls these same targets.

PY      ?= python3
CONFIG  ?= config/woo.configuration.json
RAW     ?=

.PHONY: lint sanitize test all help

help:
	@echo "make lint                 # gate: fail on runtime pollution / dangling refs"
	@echo "make sanitize             # strip pollution from \$$CONFIG in place"
	@echo "make sanitize RAW=x.json  # clean a fresh export x.json into \$$CONFIG"
	@echo "make test                 # run unit tests"
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

all: lint test
