#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/gitlib.py — minimal Forgejo/Codeberg REST client (stdlib `urllib` only).
#
# Opens a pull request on the tenants repo (Nextcloud-base) from the portal,
# without a `git` binary, a working copy, or any third-party SDK — preserving the
# repo's deliberate zero-dependency posture. Three calls: create a branch off the
# base, put one file on it, open a PR. The PR's html_url is the link returned to
# the operator's form.
#
# The git-write identity is whatever account owns FORGEJO_TOKEN (MVP: the
# maintainer's `write:repository` PAT; hardening: a dedicated bot). The clicking
# operator is recorded separately as `requested-by` in the commit + PR body.
#
# Config (env, read per call so tests can set it):
#   FORGEJO_API_URL   e.g. https://codeberg.org/api/v1   (no trailing slash)
#   FORGEJO_TOKEN     a write:repository access token     (NEVER logged)
#   TENANTS_REPO      owner/name, e.g. conduction/Nextcloud-base
#   TENANTS_BASE      base branch, default "main"
#
# Writes: opens a branch + commit + PR on the remote repo. No local writes.
# Idempotent: no — a second call with the same tenant name hits 409 (branch/file
#   exists), surfaced as GitlibError so the caller can tell the operator.
# Requires: python3.8+, network egress to FORGEJO_API_URL.
"""Stdlib-only Forgejo REST client: branch -> put file -> open PR."""

import base64
import json
import os
import urllib.error
import urllib.request


class GitlibError(Exception):
    """A Forgejo API call failed. `status` is the HTTP code (0 for transport
    errors); `detail` is a short, secret-free message safe to show the operator."""

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"forgejo {status}: {detail}")


def _cfg():
    """Read config from env at call time. Missing required values raise
    GitlibError(0, ...) rather than KeyError, so the route returns a clean 4xx."""
    api = os.environ.get("FORGEJO_API_URL", "").rstrip("/")
    token = os.environ.get("FORGEJO_TOKEN", "")
    repo = os.environ.get("TENANTS_REPO", "")
    base = os.environ.get("TENANTS_BASE", "main")
    missing = [n for n, v in (("FORGEJO_API_URL", api), ("FORGEJO_TOKEN", token),
                              ("TENANTS_REPO", repo)) if not v]
    if missing:
        raise GitlibError(0, f"server misconfigured: missing {', '.join(missing)}")
    return api, token, repo, base


def _request(method, path, payload):
    """POST/GET a JSON body to the Forgejo API. Returns the parsed JSON dict.
    Raises GitlibError on any non-2xx or transport failure — never leaks the
    token into the message."""
    api, token, _repo, _base = _cfg()
    url = f"{api}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = _short_error(exc)
        raise GitlibError(exc.code, detail) from None
    except urllib.error.URLError as exc:
        raise GitlibError(0, f"cannot reach forgejo: {exc.reason}") from None


def _short_error(exc):
    """Extract a concise message from a Forgejo error body without echoing
    anything sensitive."""
    try:
        body = exc.read().decode("utf-8")
        obj = json.loads(body)
        return str(obj.get("message") or obj.get("error") or body)[:300]
    except Exception:
        return f"HTTP {exc.code}"


def create_branch(new_branch, old_branch=None):
    """Create `new_branch` off `old_branch` (default: the configured base)."""
    _api, _token, repo, base = _cfg()
    payload = {"new_branch_name": new_branch,
               "old_branch_name": old_branch or base}
    return _request("POST", f"/repos/{repo}/branches", payload)


def put_file(branch, path, content, message, author_name=None, author_email=None):
    """Create a file at `path` on `branch` with `content` (str). Author/committer
    default to the token's account; pass author_* to attribute the human."""
    _api, _token, repo, _base = _cfg()
    payload = {
        "branch": branch,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "message": message,
    }
    if author_name and author_email:
        ident = {"name": author_name, "email": author_email}
        payload["author"] = ident
        payload["committer"] = ident
    return _request("POST", f"/repos/{repo}/contents/{path}", payload)


def open_pr(head, title, body, base=None):
    """Open a PR from `head` into `base` (default: configured base). Returns the
    Forgejo PR object; callers want `number` and `html_url`."""
    _api, _token, repo, cfg_base = _cfg()
    payload = {"head": head, "base": base or cfg_base, "title": title, "body": body}
    return _request("POST", f"/repos/{repo}/pulls", payload)


def propose_file(branch, path, content, commit_message, pr_title, pr_body,
                 author_name=None, author_email=None):
    """Orchestrate branch -> put file -> PR. Returns {number, html_url}.
    Raises GitlibError (with .status) on the first failing step so the route can
    map 409 -> 'already exists' and 0 -> 'misconfigured/unreachable'."""
    create_branch(branch)
    put_file(branch, path, content, commit_message, author_name, author_email)
    pr = open_pr(branch, pr_title, pr_body)
    return {"number": pr.get("number"), "html_url": pr.get("html_url")}
