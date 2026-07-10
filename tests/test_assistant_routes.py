# SPDX-License-Identifier: EUPL-1.2
# Route-tests voor het assistent-endpoint (webgui/server.py). Skipt als
# geheel zonder Flask (systeem-python); draai in de webgui-venv:
#   webgui/.venv/bin/python -m pytest tests/test_assistant_routes.py
"""NDJSON-stream, statuscodes en de fail-closed auth-gate van /assistant."""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("flask")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import assistant  # noqa: E402
import server     # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "REQUIRE_AUTH", False)
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_assistant_page_renders(client):
    resp = client.get("/assistant")
    assert resp.status_code == 200
    assert b"Platform-assistent" in resp.data


def test_home_links_to_assistant(client):
    resp = client.get("/")
    assert b'href="/assistant"' in resp.data


def test_ask_streams_ndjson(client, monkeypatch):
    def fake_stream(question, user):
        assert question == "hoe voeg ik een tenant toe?"

        def gen():
            yield {"type": "delta", "text": "Zo: "}
            yield {"type": "sources", "sources": []}
            yield {"type": "done", "is_error": False}
        return gen()
    monkeypatch.setattr(server.assistant, "ask_stream", fake_stream)
    resp = client.post("/api/assistant/ask",
                       json={"question": "hoe voeg ik een tenant toe?"})
    assert resp.status_code == 200
    assert resp.mimetype == "application/x-ndjson"
    events = [json.loads(l) for l in resp.data.decode().strip().splitlines()]
    assert [e["type"] for e in events] == ["delta", "sources", "done"]


def test_ask_rate_limited_is_429(client, monkeypatch):
    def limited(question, user):
        raise assistant.AssistantError("limiet bereikt", http_status=429)
    monkeypatch.setattr(server.assistant, "ask_stream", limited)
    resp = client.post("/api/assistant/ask", json={"question": "x"})
    assert resp.status_code == 429
    assert "limiet" in resp.get_json()["errors"][0]


def test_ask_requires_auth_when_enabled(monkeypatch):
    monkeypatch.setattr(server, "REQUIRE_AUTH", True)
    server.app.config["TESTING"] = True
    c = server.app.test_client()
    resp = c.post("/api/assistant/ask", json={"question": "x"})
    assert resp.status_code == 403
