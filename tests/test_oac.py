# SPDX-License-Identifier: EUPL-1.2
"""Unit tests for the OpenRegister config linter/sanitizer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import oac  # noqa: E402


def _doc(**comps):
    return {"openapi": "3.0.0", "info": {"title": "t"}, "components": comps}


def test_pollution_detected_on_synchronization():
    doc = _doc(synchronizations={"s1": {"slug": "s1", "currentPage": 3, "sourceHash": "x"}})
    codes = [c for _s, c, _m in oac.lint(doc)]
    assert codes.count("pollution") == 2


def test_schema_version_is_preserved():
    doc = _doc(schemas={"adviezen": {"version": "1.0.4", "created": "2025-01-01"}})
    findings = oac.lint(doc)
    # created is pollution, version is NOT (semantic in schemas)
    assert any(c == "pollution" and "created" in m for _s, c, m in findings)
    assert not any(c == "pollution" and "version" in m for _s, c, m in findings)
    oac.sanitize(doc)
    assert doc["components"]["schemas"]["adviezen"]["version"] == "1.0.4"
    assert "created" not in doc["components"]["schemas"]["adviezen"]


def test_sync_version_is_stripped():
    doc = _doc(synchronizations={"s1": {"version": "0.0.11"}})
    oac.sanitize(doc)
    assert "version" not in doc["components"]["synchronizations"]["s1"]


def test_dangling_sourceid_reference():
    doc = _doc(
        sources={"real": {"slug": "real"}},
        synchronizations={"s1": {"sourceId": "ghost"}},
    )
    assert any(c == "dangling-ref" for _s, c, _m in oac.lint(doc))


def test_valid_reference_passes():
    doc = _doc(
        sources={"real": {"slug": "real"}},
        registers={"woo": {"slug": "woo"}},
        schemas={"conv": {"slug": "conv"}},
        synchronizations={"s1": {"sourceId": "real", "targetId": "woo/conv",
                                 "targetType": "register/schema"}},
    )
    assert not any(c == "dangling-ref" for _s, c, _m in oac.lint(doc))


def test_sanitize_is_idempotent():
    doc = _doc(synchronizations={"s1": {"currentPage": 1, "created": "x", "sourceId": "a"}},
               sources={"a": {"slug": "a"}})
    first = oac.sanitize(doc)
    second = oac.sanitize(doc)
    assert first == 2 and second == 0  # currentPage + created stripped, sourceId kept
    assert doc["components"]["synchronizations"]["s1"] == {"sourceId": "a"}


def test_job_runtime_fields_stripped_config_kept():
    doc = _doc(jobs={"j1": {
        "name": "Sync", "interval": 1800, "isEnabled": True,
        "arguments": {"synchronizationId": "s1"},
        "lastRun": "x", "nextRun": "y", "status": "z", "jobListId": "105",
        "version": "0.0.1", "executionTime": 5, "created": "c",
    }})
    oac.sanitize(doc)
    job = doc["components"]["jobs"]["j1"]
    assert job == {"name": "Sync", "interval": 1800, "isEnabled": True,
                   "arguments": {"synchronizationId": "s1"}}


def test_job_dangling_synchronization_reference():
    doc = _doc(
        synchronizations={"s1": {"slug": "s1"}},
        jobs={"j1": {"jobClass": "OCA\\OpenConnector\\Action\\SynchronizationAction",
                     "arguments": {"synchronizationId": "ghost"}}},
    )
    assert any(c == "dangling-ref" and "synchronizationId" in m
               for _s, c, m in oac.lint(doc))


def test_job_valid_synchronization_slug_passes():
    doc = _doc(
        synchronizations={"s1": {"slug": "mysync"}},
        jobs={"j1": {"jobClass": "OCA\\OpenConnector\\Action\\SynchronizationAction",
                     "arguments": {"synchronizationId": "mysync"}}},
    )
    assert not any(c == "dangling-ref" for _s, c, _m in oac.lint(doc))


def test_invalid_authorization_action_flagged():
    doc = _doc(schemas={"s1": {"authorization": {"read": [], "inheritFromPublic": True}}})
    findings = oac.lint(doc)
    assert any(c == "bad-authorization" and "inheritFromPublic" in m
               for _s, c, m in findings)


def test_valid_authorization_actions_pass():
    doc = _doc(schemas={"s1": {"authorization": {"read": [], "create": [], "update": [], "delete": []}}})
    assert not any(c == "bad-authorization" for _s, c, _m in oac.lint(doc))


def test_objects_data_leak_warns_but_does_not_delete():
    doc = _doc(objects=[{"id": 1, "created": "x"}])
    assert any(c == "data-leak" for _s, c, _m in oac.lint(doc))
    oac.sanitize(doc)
    assert len(doc["components"]["objects"]) == 1  # data is never auto-deleted
