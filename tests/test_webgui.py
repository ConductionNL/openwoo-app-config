# SPDX-License-Identifier: EUPL-1.2
# Phase-1 tests for the hosted Flask control-plane (webgui/server.py).
#
# Skips entirely when Flask is not importable (the system-python `make test`
# run has no Flask); the webgui venv has it, so run there to exercise these:
#   webgui/.venv/bin/python -m pytest tests/test_webgui.py
"""Route + audit-logging tests for webgui/server.py (no real subprocess)."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import server  # noqa: E402


@pytest.fixture
def client():
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.data == b"ok\n"


def test_index_renders_form(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"OpenWoo tenant provisioning" in resp.data
    assert b'name="base"' in resp.data


def test_provision_missing_base_is_400(client):
    # build_command() raises ValueError without a base URL -> 400, no subprocess.
    resp = client.post("/provision", data={"user": "admin"})
    assert resp.status_code == 400
    assert b"error:" in resp.data


def test_provision_streams_subprocess_output(client, monkeypatch):
    """POST /provision spawns build_command()'s argv and streams stdout back.
    The real provision.py is replaced by a fake Popen so the test stays offline."""
    captured = {}

    class FakePopen:
        def __init__(self, argv, env=None, cwd=None, **kw):
            captured["argv"] = argv
            captured["env"] = env
            self.stdout = iter(["step one\n", "step two\n"])
            self.returncode = 0

        def wait(self):
            return 0

    monkeypatch.setattr(server.subprocess, "Popen", FakePopen)

    resp = client.post("/provision", data={
        "base": "https://canary.accept.commonground.nu",
        "user": "admin",
        "password": "s3cret",
    })
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "step one" in body and "step two" in body
    assert "exit code 0" in body
    # secret must travel via env, never argv
    assert "s3cret" not in " ".join(captured["argv"])
    assert "s3cret" in (captured["env"] or {}).get("GUI_PROVISION_PASSWORD", "")


def test_current_user_reads_proxy_header(client):
    with server.app.test_request_context(headers={"X-Forwarded-Email": "op@example.org"}):
        assert server.current_user() == "op@example.org"
    with server.app.test_request_context():
        assert server.current_user() == "-"
