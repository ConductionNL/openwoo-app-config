# SPDX-License-Identifier: EUPL-1.2
"""Contract tests on the canonical config itself, not on the tooling.

Each test guards a value that a fresh tenant export can silently drop, so
`make sanitize RAW=...` followed by `make test` catches it before review.
"""

import json
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config" / "woo.configuration.json"


def _schemas():
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    return doc["components"]["schemas"]


def test_every_woo_publication_schema_takes_its_slug_from_kenmerk():
    # opencatalogi#1592 / openregister#4037: a publication's public link is its
    # slug, built from the case number in `kenmerk`. An OpenRegister without
    # the fix of openregister#4774 drops `objectSlugField` on save, so an
    # export from such a tenant comes back without it.
    publication_schemas = {
        key: schema
        for key, schema in _schemas().items()
        if "kenmerk" in (schema.get("properties") or {})
    }
    assert publication_schemas, "no schema with a kenmerk property: wrong config?"

    missing = sorted(
        key
        for key, schema in publication_schemas.items()
        if (schema.get("configuration") or {}).get("objectSlugField") != "kenmerk"
    )
    assert missing == [], (
        "configuration.objectSlugField must be 'kenmerk' on every WOO publication "
        f"schema; missing or different on: {', '.join(missing)}"
    )
