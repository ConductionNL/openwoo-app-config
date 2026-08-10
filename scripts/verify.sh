#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/verify.sh — snelle functionele verificatie (pre-push gate).
#
# Draait de statische gate van deze repo: config-lint (pollution,
# dangling refs, authorization), de unit tests van linter/sanitizer en
# provisioner, en shellcheck over de eigen shell-scripts. Dry-run only; de
# functionele layer-2-test (`make functional`, docker) is bewust GEEN
# onderdeel — te traag voor een gate.
#
# Writes: read-only
# Idempotent: yes
# Requires: python3, make, shellcheck
#
# Usage:
#   ./scripts/verify.sh

set -euo pipefail

cd "$(dirname "$0")/.."

make lint
make test

# Doc-assertion (docs-claims): elk make-target dat de docs noemen
# bestaat — een how-to met een rot commando is anders stilletjes gelogen.
python3 - <<'PYEOF'
import pathlib
import re
import sys

targets = set(re.findall(r"^([a-z][a-z-]*):", pathlib.Path("Makefile").read_text(), re.M))
docs = "\n".join(p.read_text(errors="replace")
                 for p in pathlib.Path("docs").rglob("*.md"))
docs += pathlib.Path("README.md").read_text(errors="replace")
# Alleen code-context telt (regel in codeblock of inline `make x`),
# anders matcht proza als "make targets".
mentioned = set(re.findall(r"^\s*make ([a-z][a-z-]*)$", docs, re.M))
mentioned |= set(re.findall(r"`make ([a-z][a-z-]*)`", docs))
missing = sorted(mentioned - targets)
if missing:
    print(f"doc-assertion FAALT: docs noemen onbestaande make-targets: "
          f"{', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
print(f"doc-assertion OK ({len(mentioned)} genoemde make-targets bestaan)")
PYEOF

# Shellcheck over alle shell-scripts van deze repo, zoals react-base dat doet.
# Volledige dekking: alle scripts zijn op dit moment schoon, dus er is geen
# reden om de scope te beperken.
mapfile -t scripts < <(find scripts -name '*.sh' -type f | sort)
shellcheck "${scripts[@]}"
echo "shellcheck OK (${#scripts[@]} scripts)"

echo "verify: OK (lint + unit tests + doc-assertion + shellcheck)"
