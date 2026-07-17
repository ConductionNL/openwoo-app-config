#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# webgui/gitlib.py — minimal GitHub REST client (stdlib `urllib` only).
#
# Opens a pull request on the tenants repo (Nextcloud-base) from the portal,
# without a `git` binary, a working copy, or any third-party SDK — preserving the
# repo's deliberate zero-dependency posture. Four calls: resolve the base ref,
# create a branch off it, put one file on it, open a PR. The PR's html_url is the
# link returned to the operator's form.
#
# Geport van de Forgejo/Codeberg-API naar de GitHub-API (2026-07-17, migratie
# Codeberg→GitHub). Verschillen t.o.v. Forgejo, hier geabsorbeerd zodat de
# aanroepende routes (server.py) ongewijzigd blijven:
#   - branch aanmaken = 2 calls (GET base-ref-sha, POST git/refs) i.p.v. 1;
#   - bestand schrijven = PUT i.p.v. POST;
#   - "branch bestaat al" = HTTP 422 bij GitHub; wordt hier genormaliseerd
#     naar 409 zodat de bestaande tenant-in-flight-afhandeling blijft werken.
#
# The git-write identity is whatever account owns GITHUB_TOKEN (MVP: the
# maintainer's PAT with contents+pull-requests write on the tenants repo;
# hardening: a dedicated bot / GitHub App). The clicking operator is recorded
# separately as `requested-by` in the commit + PR body.
#
# Config (env, read per call so tests can set it):
#   GITHUB_API_URL    default https://api.github.com   (no trailing slash)
#   GITHUB_TOKEN      a PAT with write access            (NEVER logged)
#   TENANTS_REPO      owner/name, e.g. ConductionNL/Nextcloud-base
#   TENANTS_BASE      base branch, default "main"
#
# Writes: opens a branch + commit + PR on the remote repo. No local writes.
# Idempotent: no — a second call with the same tenant name hits 409 (branch
#   exists, na 422-normalisatie), surfaced as GitlibError so the caller can tell
#   the operator.
# Requires: python3.8+, network egress to GITHUB_API_URL.
"""Stdlib-only GitHub REST client: base ref -> branch -> put file -> open PR."""

import base64
import json
import os
import urllib.error
import urllib.request

_DEFAULT_API = "https://api.github.com"


class GitlibError(Exception):
    """A GitHub API call failed. `status` is the HTTP code (0 for transport
    errors); `detail` is a short, secret-free message safe to show the operator."""

    def __init__(self, status, detail):
        self.status = status
        self.detail = detail
        super().__init__(f"github {status}: {detail}")


def _cfg():
    """Read config from env at call time. Missing required values raise
    GitlibError(0, ...) rather than KeyError, so the route returns a clean 4xx."""
    api = os.environ.get("GITHUB_API_URL", _DEFAULT_API).rstrip("/")
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("TENANTS_REPO", "")
    base = os.environ.get("TENANTS_BASE", "main")
    missing = [n for n, v in (("GITHUB_TOKEN", token),
                              ("TENANTS_REPO", repo)) if not v]
    if missing:
        raise GitlibError(0, f"server misconfigured: missing {', '.join(missing)}")
    return api, token, repo, base


def _request(method, path, payload):
    """Send a JSON body to the GitHub API. Returns the parsed JSON dict/list.
    Raises GitlibError on any non-2xx or transport failure — never leaks the
    token into the message."""
    api, token, _repo, _base = _cfg()
    url = f"{api}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "openwoo-provisioner")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = _short_error(exc)
        raise GitlibError(exc.code, detail) from None
    except urllib.error.URLError as exc:
        raise GitlibError(0, f"cannot reach github: {exc.reason}") from None


def _short_error(exc):
    """Extract a concise message from a GitHub error body without echoing
    anything sensitive."""
    try:
        body = exc.read().decode("utf-8")
        obj = json.loads(body)
        return str(obj.get("message") or obj.get("error") or body)[:300]
    except Exception:
        return f"HTTP {exc.code}"


def _ref_sha(branch):
    """Commit sha at the tip of `branch` (via GET git/ref)."""
    _api, _token, repo, _base = _cfg()
    data = _request("GET", f"/repos/{repo}/git/ref/heads/{branch}", None)
    sha = (data.get("object") or {}).get("sha") if isinstance(data, dict) else None
    if not sha:
        raise GitlibError(404, f"branch not found: {branch}")
    return sha


def create_branch(new_branch, old_branch=None):
    """Create `new_branch` off `old_branch` (default: the configured base).
    GitHub meldt een bestaande branch als 422 'Reference already exists';
    genormaliseerd naar 409 zodat server.py's tenant-in-flight-mapping
    (409 → 'bestaat al') identiek blijft aan het Forgejo-tijdperk."""
    _api, _token, repo, base = _cfg()
    sha = _ref_sha(old_branch or base)
    payload = {"ref": f"refs/heads/{new_branch}", "sha": sha}
    try:
        return _request("POST", f"/repos/{repo}/git/refs", payload)
    except GitlibError as exc:
        if exc.status == 422 and "already exists" in exc.detail.lower():
            raise GitlibError(409, exc.detail) from None
        raise


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
    return _request("PUT", f"/repos/{repo}/contents/{path}", payload)


def open_pr(head, title, body, base=None):
    """Open a PR from `head` into `base` (default: configured base). Returns the
    GitHub PR object; callers want `number` and `html_url`."""
    _api, _token, repo, cfg_base = _cfg()
    payload = {"head": head, "base": base or cfg_base, "title": title, "body": body}
    return _request("POST", f"/repos/{repo}/pulls", payload)


def list_prs(branch_prefix="add-tenant/", limit=20):
    """Recent tenant PRs (those opened by this portal use the `add-tenant/<tenant>`
    head branch). Returns [{number, title, state, merged, html_url, tenant}], newest
    first. Used by the dashboard. GitHub kent geen `merged`-bool in de lijstweergave;
    afgeleid uit `merged_at`."""
    _api, _token, repo, _base = _cfg()
    data = _request(
        "GET",
        f"/repos/{repo}/pulls?state=all&sort=updated&direction=desc&per_page={int(limit)}",
        None)
    rows = data if isinstance(data, list) else []
    out = []
    for p in rows:
        head = (p.get("head") or {}).get("ref", "")
        if branch_prefix and not head.startswith(branch_prefix):
            continue
        out.append({
            "number": p.get("number"),
            "title": p.get("title"),
            "state": p.get("state"),
            "merged": bool(p.get("merged_at")),
            "html_url": p.get("html_url"),
            "tenant": head[len(branch_prefix):] if head.startswith(branch_prefix) else None,
        })
    return out


def get_pr(number):
    """Fetch one PR's state for the status poll. Returns {state, merged, html_url}
    (state is 'open'/'closed'; merged distinguishes merged from just-closed)."""
    _api, _token, repo, _base = _cfg()
    pr = _request("GET", f"/repos/{repo}/pulls/{number}", None)
    return {"state": pr.get("state"), "merged": bool(pr.get("merged")),
            "html_url": pr.get("html_url")}


def propose_file(branch, path, content, commit_message, pr_title, pr_body,
                 author_name=None, author_email=None):
    """Orchestrate branch -> put file -> PR. Returns {number, html_url}.
    Raises GitlibError (with .status) on the first failing step so the route can
    map 409 -> 'already exists' and 0 -> 'misconfigured/unreachable'."""
    create_branch(branch)
    put_file(branch, path, content, commit_message, author_name, author_email)
    pr = open_pr(branch, pr_title, pr_body)
    return {"number": pr.get("number"), "html_url": pr.get("html_url")}


def propose_files(branch, files, commit_message, pr_title, pr_body,
                  author_name=None, author_email=None):
    """Batch: one branch, multiple files (one commit each), one PR. `files` is a
    list of (path, content). Returns {number, html_url}."""
    create_branch(branch)
    for path, content in files:
        put_file(branch, path, content, commit_message, author_name, author_email)
    pr = open_pr(branch, pr_title, pr_body)
    return {"number": pr.get("number"), "html_url": pr.get("html_url")}


def get_file_sha(path, ref=None):
    """Blob sha of a file on `ref` (default base). Raises GitlibError(404) if absent."""
    _api, _token, repo, base = _cfg()
    data = _request("GET", f"/repos/{repo}/contents/{path}?ref={ref or base}", None)
    sha = data.get("sha") if isinstance(data, dict) else None
    if not sha:
        raise GitlibError(404, f"file not found: {path}")
    return sha


def propose_deletion(branch, path, commit_message, pr_title, pr_body,
                     author_name=None, author_email=None):
    """Open a PR that DELETES `path`. Returns {number, html_url}. Raises
    GitlibError(404) if the file doesn't exist (nothing to delete)."""
    _api, _token, repo, base = _cfg()
    sha = get_file_sha(path)                       # from base; branch == base at creation
    create_branch(branch)
    payload = {"branch": branch, "sha": sha, "message": commit_message}
    if author_name and author_email:
        ident = {"name": author_name, "email": author_email}
        payload["author"] = ident
        payload["committer"] = ident
    _request("DELETE", f"/repos/{repo}/contents/{path}", payload)
    pr = open_pr(branch, pr_title, pr_body)
    return {"number": pr.get("number"), "html_url": pr.get("html_url")}
