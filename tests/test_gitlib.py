# SPDX-License-Identifier: EUPL-1.2
# Tests for webgui/gitlib.py — stdlib-only GitHub client, fully offline.
#
# urllib.request.urlopen is monkeypatched so no network is touched; tests assert
# the request sequence, headers (token present), and error mapping.
"""Offline tests for the GitHub REST client (base ref -> branch -> put file -> PR)."""

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
    monkeypatch.setenv("GITHUB_API_URL", "https://api.github.com")
    monkeypatch.setenv("GITHUB_TOKEN", "tok-secret")
    monkeypatch.setenv("TENANTS_REPO", "ConductionNL/Nextcloud-base")
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


_BASE_REF = {"object": {"sha": "basesha123"}}


def test_propose_file_happy_path(monkeypatch):
    calls = []
    responses = [
        _BASE_REF,                            # create_branch: GET base ref
        {},                                   # create_branch: POST git/refs
        {},                                   # put_file
        {"number": 123, "html_url": "https://github.com/ConductionNL/Nextcloud-base/pull/123"},
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
                      "html_url": "https://github.com/ConductionNL/Nextcloud-base/pull/123"}
    # four calls in order: base ref, refs, contents, pulls
    paths = [c.full_url for c in calls]
    assert paths[0].endswith("/repos/ConductionNL/Nextcloud-base/git/ref/heads/main")
    assert paths[1].endswith("/repos/ConductionNL/Nextcloud-base/git/refs")
    assert "/repos/ConductionNL/Nextcloud-base/contents/" in paths[2]
    assert paths[3].endswith("/repos/ConductionNL/Nextcloud-base/pulls")
    # the new branch is created off the resolved base sha
    ref_payload = json.loads(calls[1].data.decode("utf-8"))
    assert ref_payload == {"ref": "refs/heads/add-tenant/almere-accept",
                           "sha": "basesha123"}
    # GitHub contents API is PUT (Forgejo was POST)
    assert calls[2].get_method() == "PUT"
    # token travels as Bearer in the Authorization header
    assert calls[0].get_header("Authorization") == "Bearer tok-secret"
    assert calls[0].get_header("User-agent") == "openwoo-provisioner"


def test_propose_files_one_branch_many_files_one_pr(monkeypatch):
    calls = []
    # base ref, refs, 2x put, pr
    responses = [_BASE_REF, {}, {}, {}, {"number": 3, "html_url": "u3"}]
    monkeypatch.setattr(gitlib.urllib.request, "urlopen", _fake_urlopen(responses, calls))
    out = gitlib.propose_files("add-tenants/x",
                               [("a.yaml", "A"), ("b.yaml", "B")],
                               "msg", "title", "body")
    assert out["number"] == 3
    # 5 calls: 2 branch (ref+refs) + 2 contents + 1 pulls
    assert len(calls) == 5
    assert calls[1].full_url.endswith("/git/refs")
    assert calls[4].full_url.endswith("/pulls")


def test_propose_deletion_gets_sha_then_deletes(monkeypatch):
    calls = []
    # get sha, base ref, refs, delete, pr
    responses = [{"sha": "abc123"}, _BASE_REF, {}, {}, {"number": 4, "html_url": "u4"}]
    monkeypatch.setattr(gitlib.urllib.request, "urlopen", _fake_urlopen(responses, calls))
    out = gitlib.propose_deletion("delete-tenant/x",
                                  "nextcloud-platform/values/tenants/tenant-x-accept.yaml",
                                  "msg", "title", "body")
    assert out["number"] == 4
    assert calls[0].get_method() == "GET"      # get_file_sha
    assert calls[3].get_method() == "DELETE"   # delete_file
    delete_payload = json.loads(calls[3].data.decode("utf-8"))
    assert delete_payload["sha"] == "abc123"


def test_list_prs_filters_to_tenant_branches(monkeypatch):
    # GitHub list objects carry merged_at (no `merged` bool); gitlib derives it.
    rows = [
        {"number": 9, "title": "add tenant: almere-accept", "state": "closed",
         "merged_at": "2026-07-17T09:00:00Z",
         "html_url": "u9", "head": {"ref": "add-tenant/almere-accept"}},
        {"number": 8, "title": "unrelated", "state": "open", "merged_at": None,
         "html_url": "u8", "head": {"ref": "feature/x"}},
    ]
    monkeypatch.setattr(gitlib.urllib.request, "urlopen", _fake_urlopen([rows], []))
    out = gitlib.list_prs()
    assert len(out) == 1 and out[0]["number"] == 9 and out[0]["tenant"] == "almere-accept"
    assert out[0]["merged"] is True


def test_get_pr_returns_state(monkeypatch):
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([{"state": "open", "merged": False,
                                        "html_url": "https://x/pull/9"}], []))
    pr = gitlib.get_pr("9")
    assert pr == {"state": "open", "merged": False, "html_url": "https://x/pull/9"}


def test_branch_exists_422_normalises_to_409(monkeypatch):
    # GitHub signals a duplicate branch as 422 "Reference already exists";
    # gitlib must normalise that to 409 so server.py's in-flight mapping holds.
    err = urllib.error.HTTPError(
        url="x", code=422, msg="unprocessable", hdrs=None,
        fp=io.BytesIO(json.dumps({"message": "Reference already exists"}).encode()))
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([_BASE_REF, err], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.create_branch("add-tenant/almere-accept")
    assert ei.value.status == 409
    assert "already exists" in ei.value.detail.lower()


def test_other_422_is_not_masked(monkeypatch):
    err = urllib.error.HTTPError(
        url="x", code=422, msg="unprocessable", hdrs=None,
        fp=io.BytesIO(json.dumps({"message": "Validation Failed"}).encode()))
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([_BASE_REF, err], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.create_branch("add-tenant/almere-accept")
    assert ei.value.status == 422


def test_urlerror_maps_to_status_zero(monkeypatch):
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([urllib.error.URLError("no route")], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.open_pr("head", "t", "b")
    assert ei.value.status == 0
    assert "cannot reach github" in ei.value.detail


def test_missing_config_raises_before_network(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.create_branch("any")
    assert ei.value.status == 0
    assert "GITHUB_TOKEN" in ei.value.detail


def test_api_url_defaults_to_github(monkeypatch):
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    calls = []
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([{"state": "open", "merged": False,
                                        "html_url": "u"}], calls))
    gitlib.get_pr(1)
    assert calls[0].full_url.startswith("https://api.github.com/")


def test_error_message_never_leaks_token(monkeypatch):
    err = urllib.error.HTTPError(url="x", code=403, msg="forbidden", hdrs=None,
                                 fp=io.BytesIO(b"{}"))
    monkeypatch.setattr(gitlib.urllib.request, "urlopen",
                        _fake_urlopen([err], []))
    with pytest.raises(gitlib.GitlibError) as ei:
        gitlib.open_pr("head", "t", "b")
    assert "tok-secret" not in ei.value.detail
