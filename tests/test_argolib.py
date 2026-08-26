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
    # `images` is leeg maar aanwezig: een Application zonder status.summary is
    # geen fout, en de guard moet een lege lijst kunnen verwerken.
    assert argolib.app_status("nc-almere-accept") == {
        "exists": True, "sync": "Synced", "health": "Healthy", "images": []}


def test_app_status_reports_images(monkeypatch):
    """`status.summary.images` komt mee, zonder tweede API-call.

    Dit is de kruiscontrole van de image-downgrade-guard: git zegt wat er hoort
    te draaien, dit zegt wat Argo ziet.
    """
    monkeypatch.setattr(argolib.urllib.request, "urlopen", _urlopen(
        {"status": {"sync": {"status": "Synced"}, "health": {"status": "Healthy"},
                    "summary": {"images": [
                        "ghcr.io/conductionnl/nextcloud-images:32.0.13-fpm",
                        "docker.io/conduction2022/woo-website-v2:V1.0.260422"]}}}))
    status = argolib.app_status("nc-almere-accept")
    assert status["images"] == [
        "ghcr.io/conductionnl/nextcloud-images:32.0.13-fpm",
        "docker.io/conduction2022/woo-website-v2:V1.0.260422"]


def test_image_for_repository_selects_by_path_not_position():
    """Selecteren op positie breekt zodra de chart een image toevoegt."""
    images = ["docker.io/conduction2022/woo-website-v2:V1.0.260422",
              "ghcr.io/conductionnl/nextcloud-images:32.0.13-fpm"]
    assert argolib.image_for_repository(images, "conductionnl/nextcloud-images") == \
        "ghcr.io/conductionnl/nextcloud-images:32.0.13-fpm"
    # Zonder registry-prefix in de image-reference werkt het ook.
    assert argolib.image_for_repository(["nextcloud:32.0.13-fpm"], "nextcloud") == \
        "nextcloud:32.0.13-fpm"
    # Geen match en geen repository geven None, niet de eerste image.
    assert argolib.image_for_repository(images, "does/not-exist") is None
    assert argolib.image_for_repository(images, "") is None


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
