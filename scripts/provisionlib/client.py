# SPDX-License-Identifier: EUPL-1.2
# role: library
#
# scripts/provisionlib/client.py — minimal Nextcloud API client.
#
# Basic-auth + OCS-APIREQUEST, JSON in/out, over the Python stdlib only (no
# third-party HTTP lib, by design — auditability + no supply-chain surface).
# ProvisionError is the single error type every step raises.
"""Thin basic-auth JSON HTTP client and the ProvisionError type."""

import base64
import json
import urllib.error
import urllib.request

from .helpers import log

# --- Thin HTTP client (basic-auth, JSON) ---


class Client:
    """Minimal Nextcloud API client: basic-auth + OCS-APIREQUEST, JSON in/out."""

    def __init__(self, base, user, password, host_header=None):
        self.base = base.rstrip("/")
        self.user = user
        # Override the Host header (connect to --base, present a trusted domain).
        # Needed in-cluster: hitting http://nextcloud:8080 sends Host nextcloud:8080,
        # which Nextcloud rejects (not a trusted_domain). Set --host-header to the
        # tenant's domain (a trusted_domain) so requests are accepted.
        self.host_header = host_header
        if self.base.startswith("http://") and not any(
            h in self.base for h in ("localhost", "127.0.0.1")
        ):
            log(f"WARNING: {self.base} is not HTTPS — basic-auth credentials "
                f"would be sent in cleartext")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.auth_header = f"Basic {token}"

    def _request(self, method, path, body=None):
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self.auth_header)
        req.add_header("OCS-APIREQUEST", "true")
        req.add_header("Accept", "application/json")
        if self.host_header:
            req.add_header("Host", self.host_header)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise ProvisionError(
                f"{method} {path} -> HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise ProvisionError(f"{method} {path} -> {exc.reason}") from exc
        if not raw.strip():
            # A 2xx with an empty body (e.g. DELETE -> 204 No Content) is a
            # success with nothing to parse. Return {} so callers don't trip the
            # "non-JSON response" guard below (which is meant for HTML login pages).
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # A login/HTML page instead of JSON means the route refused our auth.
            raise ProvisionError(
                f"{method} {path} -> non-JSON response (auth refused?): {raw[:120]}"
            )

    def get(self, path):
        return self._request("GET", path)

    def put(self, path, body):
        return self._request("PUT", path, body)

    def post(self, path, body=None):
        return self._request("POST", path, body)

    def delete(self, path):
        return self._request("DELETE", path)

    def post_file(self, path, filename, content):
        """Multipart file upload (for the @NoCSRFRequired config import). Returns text."""
        boundary = "----provisionfileboundary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.base}{path}", data=body, method="POST")
        req.add_header("Authorization", self.auth_header)
        req.add_header("OCS-APIREQUEST", "true")
        if self.host_header:
            req.add_header("Host", self.host_header)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise ProvisionError(f"POST {path} (file) -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProvisionError(f"POST {path} (file) -> {exc.reason}") from exc


class ProvisionError(Exception):
    """A provisioning step failed (HTTP error, refused auth, or failed assert)."""
