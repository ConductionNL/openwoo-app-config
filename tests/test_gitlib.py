# SPDX-License-Identifier: EUPL-1.2
# Tests for webgui/gitlib.py — stdlib-only Forgejo client, fully offline.
#
# urllib.request.urlopen is monkeypatched so no network is touched; tests assert
# the request sequence, headers (token present), and error mapping.
"""Offline tests for the Forgejo REST client (branch -> put file -> PR)."""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import gitlib  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FORGEJO_API_URL", "https://codeberg.org/api/v1")
    monkeypatch.setenv("FORGEJO_TOKEN", "tok-secret")
    monkeypatch.setenv("TENANTS_REPO", "conduction/Nextcloud-base")
    monkeypatch.setenv("TENANTS_BASE", "main")


class _Resp:
    """Minimal context-manager response for a fake urlopen."""
    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(responses, calls):
    """Return a urlopen that records each Request and pops a queued response.
    A queued item may be an Exception class/instance to raise."""
    def _open(req, timeout=None):
        calls.append(req)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)
    return _open


def test_propose_file_happy_path(monkeypatch):
    calls = []
    responses = [
        {},                                   # create_branch
        {},                                   # put_file
        {"number": 123, "html_url": "https://codeberg.org/conduction/Nextcloud-base/pulls/123"},
    ]
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen(responses, calls))

    result = gitlib.propose_file(
        branch="add-tenant/almere-accept",
        path="nextcloud-platform/values/tenants/tenant-almere-accept.yaml",
        content="---\ntenant:\n  name: almere-accept\n",
        commit_message="add tenant: almere-accept",
        pr_title="add tenant: almere-accept",
        pr_body="body")

    assert result == {"number": 123,
                      "html_url": "https://codeberg.org/conduction/Nextcloud-base/pulls/123"}
    # three calls in order: branches, contents, pulls
    paths = [c.full_url for c in calls]
    assert paths[0].endswith("/repos/conduction/Nextcloud-base/branches")
    assert "/repos/conduction/Nextcloud-base/contents/" in paths[1]
    assert paths[2].endswith("/repos/conduction/Nextcloud-base/pulls")
    # token travels in the Authorization header
    assert calls[0].get_header("Authorization") == "token tok-secret"


def test_get_pr_returns_state(monkeypatch):
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([{"state": "open", "merged": False,
                                        "html_url": "https://x/pulls/9"}], []))
    pr = gitlib.get_pr("9")
    assert pr == {"state": "open", "merged": False, "html_url": "https://x/pulls/9"}


def test_409_branch_exists_maps_to_status(monkeypatch):
    err = urllib.error.HTTPError(
        url="x", code=409, msg="conflict", hdrs=None,
        fp=io.BytesIO(json.dumps({"message": "branch already exists"}).encode()))
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([err], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.create_branch("add-tenant/almere-accept")
    assert ei.value.status == 409
    assert "already exists" in ei.value.detail


def test_urlerror_maps_to_status_zero(monkeypatch):
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([urllib.error.URLError("no route")], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.open_pr("head", "t", "b")
    assert ei.value.status == 0
    assert "cannot reach forgejo" in ei.value.detail


def test_missing_config_raises_before_network(monkeypatch):
    monkeypatch.delenv("FORGEJO_TOKEN", raising=False)
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.create_branch("any")
    assert ei.value.status == 0
    assert "FORGEJO_TOKEN" in ei.value.detail


def test_error_message_never_leaks_token(monkeypatch):
    err = urllib.error.HTTPError(url="x", code=403, msg="forbidden", hdrs=None,
                                 fp=io.BytesIO(b"{}"))
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([err], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.open_pr("head", "t", "b")
    assert "tok-secret" not in ei.value.detail
