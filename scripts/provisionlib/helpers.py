# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/helpers.py — pure, unit-tested helpers (no live stack).
#
# Config-shape readers, list/slug utilities, and the stderr logger. Pure
# functions: deterministic, no I/O beyond log() and load_config(), so they are
# exercised directly in the unit tests without a running instance.
"""Pure helpers: config readers, slug/list utilities, and the logger."""

import json
import sys

from .constants import DUMMY_APIKEY_PREFIX

def load_config(path):
    """Load the configuration JSON document."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def bucket_items(doc, bucket):
    """Return a bucket's entities as a list, whether stored as list or dict."""
    raw = doc.get("components", {}).get(bucket)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return list(raw.values())
    return []


def config_source_slugs(doc):
    """Slugs of every source defined in the config (skip entries without one)."""
    return config_slugs(doc, "sources")


def results_list(payload):
    """Unwrap a list endpoint response: {"results": [...]} or a bare list."""
    if isinstance(payload, dict):
        inner = payload.get("results", [])
        return inner if isinstance(inner, list) else []
    return payload if isinstance(payload, list) else []


def find_by_slug(items, slug):
    """First item whose slug (or, as a fallback, name) matches; else None."""
    for item in items:
        if item.get("slug") == slug:
            return item
    for item in items:
        if item.get("name") == slug:
            return item
    return None


def dummy_apikey(slug):
    """Deterministic, obviously-fake apikey for a source slug."""
    return f"{DUMMY_APIKEY_PREFIX}{slug}"


def config_slugs(doc, bucket):
    """Slugs of every entity in a config bucket (skip entries without one)."""
    return [e["slug"] for e in bucket_items(doc, bucket) if e.get("slug")]


def slug_to_id(items):
    """Map slug -> id for a list of entities (skips entries without a slug)."""
    return {i.get("slug"): i.get("id") for i in items if i.get("slug")}


def merge_header(configuration, header, value):
    """Return the source configuration with `header` set, preserving the rest.

    OpenConnector serializes an empty configuration as a list; treat any
    non-dict as empty so we never clobber existing headers on PUT.
    """
    base = dict(configuration) if isinstance(configuration, dict) else {}
    base[header] = value
    return base


def log(msg):
    print(f"==> {msg}", file=sys.stderr)
