#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/verify.sh — snelle functionele verificatie (pre-push gate).
#
# Draait de statische gate van deze repo: config-lint (pollution,
# dangling refs, authorization) en de unit tests van linter/sanitizer en
# provisioner. Dry-run only; de functionele layer-2-test (`make
# functional`, docker) is bewust GEEN onderdeel — te traag voor een gate.
#
# Writes: read-only
# Idempotent: yes
# Requires: python3, make
#
# Usage:
#   ./scripts/verify.sh

set -euo pipefail

cd "$(dirname "$0")/.."

make lint
make test
echo "verify: OK (lint + unit tests)"
