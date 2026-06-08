#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# scripts/oac.py — lint and sanitize OpenRegister configuration exports.
#
# An OpenRegister "configuration export" is an OpenAPI-enveloped JSON document
# (openapi / info / components / x-openregister) whose `components` hold the
# platform config buckets: registers, schemas, mappings, sources, rules,
# synchronizations, endpoints, jobs, workflows, objects.
#
# When an export is taken from an instance that has *imported* into PostgreSQL,
# runtime state (sync cursors, content hashes, last-synced timestamps, created/
# updated metadata) leaks back into the export and pollutes the config. This
# tool gates against that pollution (`lint`) and strips it (`sanitize`) to
# produce a clean, portable config.
#
# Pure Python standard library — no third-party dependencies, by design
# (auditability + no supply-chain surface). See README.md for rationale.
#
# Writes: `sanitize` writes the cleaned document to --output (or stdout);
#         `lint` is read-only.
# Idempotent: yes — running sanitize twice yields the same output.
# Requires: python3.8+
#
# Usage:
#   python3 scripts/oac.py lint config/woo.configuration.json
#   python3 scripts/oac.py sanitize config/raw-export.json -o config/woo.configuration.json
#   python3 scripts/oac.py sanitize config/raw-export.json --in-place
"""Lint and sanitize OpenRegister configuration exports (stdlib only)."""

import argparse
import json
import sys
from pathlib import Path

# --- Pollution definitions (see README.md "What gets stripped, and why") ---

# Pure timestamps: always runtime noise, stripped from every entity in every bucket.
TIMESTAMP_KEYS = {"created", "updated", "dateCreated", "dateModified"}

# Runtime state stripped per bucket, IN ADDITION to TIMESTAMP_KEYS. These are the
# fields that PostgreSQL fills in during import/sync and that leak back into an
# export ("export-on-import-on-postgres"). Only the entity top level is touched.
#
# Deliberately NOT listed (kept as config/behaviour): published, depublished,
# deleted, owner, rateLimitLimit, rateLimitWindow, isEnabled — these may carry
# configuration or behavioural meaning, so we never auto-strip them.
#
# `schemas` is deliberately absent: there `version` is a SEMANTIC schema version
# (e.g. 1.0.4) and must be preserved.
BUCKET_RUNTIME_KEYS = {
    "synchronizations": {
        "currentPage", "sourceHash", "targetHash",
        "sourceLastChanged", "sourceLastChecked", "sourceLastSynced",
        "targetLastChanged", "targetLastChecked", "targetLastSynced",
        "status", "version",
    },
    "sources": {
        "lastCall", "lastSync", "objectCount",
        "rateLimitRemaining", "rateLimitReset", "status", "version",
    },
    "mappings": {"version"},
    "rules": {"version"},
    "registers": {"version", "usage"},
}

# Buckets that hold stored data records, not configuration. They should be empty
# in a config export; non-empty means data leaked in. We WARN but never delete
# data automatically (deleting records silently is dangerous).
DATA_BUCKETS = {"objects"}

KNOWN_BUCKETS = {
    "mappings", "sources", "rules", "endpoints", "synchronizations",
    "jobs", "registers", "schemas", "workflows", "objects",
}


def strip_keys_for(bucket):
    """Return the set of top-level keys to strip from entities in `bucket`."""
    return TIMESTAMP_KEYS | BUCKET_RUNTIME_KEYS.get(bucket, set())


def iter_entities(bucket_val):
    """Yield (name, entity_dict) for a bucket that is a dict-of-slug or a list."""
    if isinstance(bucket_val, dict):
        for name, ent in bucket_val.items():
            if isinstance(ent, dict):
                yield name, ent
    elif isinstance(bucket_val, list):
        for idx, ent in enumerate(bucket_val):
            if isinstance(ent, dict):
                yield str(idx), ent


def _slugs(bucket_val):
    """Collect the set of addressable names (key + .slug) for a bucket."""
    names = set()
    for name, ent in iter_entities(bucket_val):
        names.add(name)
        slug = ent.get("slug")
        if isinstance(slug, str):
            names.add(slug)
    return names


def check_refs(comps):
    """Reference-integrity checks: synchronizations must point at things that exist."""
    out = []
    sources = _slugs(comps.get("sources", {}))
    registers = _slugs(comps.get("registers", {}))
    schemas = _slugs(comps.get("schemas", {}))
    for name, sync in iter_entities(comps.get("synchronizations", {})):
        source_id = sync.get("sourceId")
        if isinstance(source_id, str) and source_id not in sources:
            out.append(("error", "dangling-ref",
                        f"synchronizations/{name}: sourceId '{source_id}' not in sources"))
        target_id = sync.get("targetId")
        target_type = sync.get("targetType") or ""
        if isinstance(target_id, str) and "register/schema" in target_type and "/" in target_id:
            reg, sch = target_id.split("/", 1)
            if reg not in registers:
                out.append(("error", "dangling-ref",
                            f"synchronizations/{name}: targetId register '{reg}' not in registers"))
            if sch not in schemas:
                out.append(("error", "dangling-ref",
                            f"synchronizations/{name}: targetId schema '{sch}' not in schemas"))
    return out


def lint(doc):
    """Return a list of (severity, code, message) findings. severity in {error,warn}."""
    findings = []

    for key in ("openapi", "info", "components"):
        if key not in doc:
            findings.append(("error", "structure", f"top-level '{key}' missing"))

    comps = doc.get("components")
    if not isinstance(comps, dict):
        findings.append(("error", "structure", "components is missing or not an object"))
        return findings

    for bucket in comps:
        if bucket not in KNOWN_BUCKETS:
            findings.append(("warn", "unknown-bucket", f"components.{bucket}: unrecognised bucket"))

    # Pollution: any runtime key present at an entity's top level.
    for bucket, val in comps.items():
        strip = strip_keys_for(bucket)
        for name, ent in iter_entities(val):
            for key in ent:
                if key in strip:
                    findings.append(("error", "pollution",
                                     f"{bucket}/{name}: runtime field '{key}'"))

    # Data leak: stored records in a config export.
    for bucket in DATA_BUCKETS:
        val = comps.get(bucket)
        count = len(val) if isinstance(val, (list, dict)) else 0
        if count:
            findings.append(("warn", "data-leak",
                             f"components.{bucket} holds {count} data record(s) — not config"))

    findings.extend(check_refs(comps))
    return findings


def sanitize(doc):
    """Strip runtime pollution in place. Return the number of fields removed."""
    comps = doc.get("components")
    removed = 0
    if isinstance(comps, dict):
        for bucket, val in comps.items():
            strip = strip_keys_for(bucket)
            for _name, ent in iter_entities(val):
                for key in list(ent):
                    if key in strip:
                        del ent[key]
                        removed += 1
    return removed


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump(doc, fh):
    json.dump(doc, fh, indent=2, ensure_ascii=False)
    fh.write("\n")


def cmd_lint(args):
    findings = lint(_load(args.input))
    errors = [f for f in findings if f[0] == "error"]
    warns = [f for f in findings if f[0] == "warn"]
    if not args.quiet:
        for sev, code, msg in findings:
            print(f"{sev.upper():5} [{code}] {msg}", file=sys.stderr)
    print(f"{args.input}: {len(errors)} error(s), {len(warns)} warning(s)", file=sys.stderr)
    return 1 if errors else 0


def cmd_sanitize(args):
    doc = _load(args.input)
    removed = sanitize(doc)
    if args.in_place:
        with open(args.input, "w", encoding="utf-8") as fh:
            _dump(doc, fh)
        dest = args.input
    elif args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            _dump(doc, fh)
        dest = args.output
    else:
        _dump(doc, sys.stdout)
        dest = "(stdout)"
    print(f"stripped {removed} runtime field(s) -> {dest}", file=sys.stderr)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lint = sub.add_parser("lint", help="gate: fail on pollution / dangling refs")
    p_lint.add_argument("input")
    p_lint.add_argument("-q", "--quiet", action="store_true", help="only print the summary line")
    p_lint.set_defaults(func=cmd_lint)

    p_san = sub.add_parser("sanitize", help="strip runtime pollution")
    p_san.add_argument("input")
    p_san.add_argument("-o", "--output", help="write cleaned config here")
    p_san.add_argument("--in-place", action="store_true", help="overwrite the input file")
    p_san.set_defaults(func=cmd_sanitize)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
