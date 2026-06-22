# SPDX-License-Identifier: EUPL-1.2
# Offline tests for webgui/argolib.py — Argo Application status reader.
"""Stub the in-cluster token/CA + urlopen so no kube API is touched."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import argolib  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_sa(monkeypatch):
    monkeypatch.setattr(argolib, "_token", lambda: "tok")
    monkeypatch.setattr(argolib, "_context", lambda: None)


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen(item):
    def _o(req, timeout=None, context=None):
        if isinstance(item, Exception):
            raise item
        return _Resp(item)
    return _o


def test_app_status_synced_healthy(monkeypatch):
    monkeypatch.setattr(argolib.urllib.request, "urlopen", _urlopen(
        {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}}))
    assert argolib.app_status("nc-almere-accept") == {
        "exists": True, "sync": "Synced", "health": "Healthy"}


def test_app_status_404_means_not_yet_generated(monkeypatch):
    err = urllib.error.HTTPError("u", 404, "not found", None, io.BytesIO(b"{}"))
    monkeypatch.setattr(argolib.urllib.request, "urlopen", _urlopen(err))
    assert argolib.app_status("nc-almere-accept") == {
        "exists": False, "sync": None, "health": None}


def test_app_status_progressing(monkeypatch):
    monkeypatch.setattr(argolib.urllib.request, "urlopen", _urlopen(
        {"status": {"sync": {"status": "Synced"}, "health": {"status": "Progressing"}}}))
    s = argolib.app_status("nc-x")
    assert s["health"] == "Progressing" and s["exists"] is True


def test_app_status_urlerror_raises(monkeypatch):
    monkeypatch.setattr(argolib.urllib.request, "urlopen",
                        _urlopen(urllib.error.URLError("down")))
    with pytest.raises(argolib.ArgoError) as ei:
        argolib.app_status("nc-x")
    assert ei.value.status == 0


def test_list_apps_filters_prefix_and_summarises(monkeypatch):
    items = {"items": [
        {"metadata": {"name": "nc-almere-accept"},
         "status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"}}},
        {"metadata": {"name": "nc-baarn-prod"},
         "status": {"sync": {"status": "OutOfSync"}, "health": {"status": "Progressing"}}},
        {"metadata": {"name": "some-other-app"}, "status": {}},  # filtered out
    ]}
    monkeypatch.setattr(argolib.urllib.request, "urlopen", _urlopen(items))
    apps = argolib.list_apps()
    assert [a["tenant"] for a in apps] == ["almere-accept", "baarn-prod"]  # sorted, prefix-stripped
    assert apps[0]["health"] == "Healthy"
