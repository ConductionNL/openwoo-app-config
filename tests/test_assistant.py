# SPDX-License-Identifier: EUPL-1.2
# Tests voor webgui/assistant.py (change add-platform-assistant, v1 strikt
# lezend). De module-tests draaien zonder Flask/claude-agent-sdk (lazy
# imports); de route-tests skippen zonder Flask — draai die in de webgui-venv:
#   webgui/.venv/bin/python -m pytest tests/test_assistant.py
"""Grenzen (rate limit, validatie), audit en de strikt-lezende tool-surface."""

import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webgui"))
import assistant  # noqa: E402


# --- rate limit (spec: per-user rate budgets) -------------------------------

def test_rate_limiter_allows_up_to_max():
    rl = assistant.RateLimiter(max_requests=3, window_seconds=60)
    for i in range(3):
        rl.check("mark@conduction.nl", now=1000.0 + i)


def test_rate_limiter_blocks_over_max_with_429():
    rl = assistant.RateLimiter(max_requests=2, window_seconds=60)
    rl.check("a@x", now=1000.0)
    rl.check("a@x", now=1001.0)
    with pytest.raises(assistant.AssistantError) as exc:
        rl.check("a@x", now=1002.0)
    assert exc.value.http_status == 429


def test_rate_limiter_window_slides():
    rl = assistant.RateLimiter(max_requests=1, window_seconds=60)
    rl.check("a@x", now=1000.0)
    rl.check("a@x", now=1061.0)  # buiten het venster: weer toegestaan


def test_rate_limiter_is_per_identity():
    rl = assistant.RateLimiter(max_requests=1, window_seconds=60)
    rl.check("a@x", now=1000.0)
    rl.check("b@x", now=1000.0)  # andere identiteit, eigen budget


# --- validatie (eager, vóór de stream) ---------------------------------------

def test_empty_question_rejected():
    with pytest.raises(assistant.AssistantError):
        assistant.ask_stream("   ", "a@x")


def test_too_long_question_rejected():
    with pytest.raises(assistant.AssistantError):
        assistant.ask_stream("x" * (assistant.MAX_QUESTION_CHARS + 1), "a@x")


# --- strikt-lezende tool-surface (spec: prompt-injected mutation attempt) ----

def test_allowed_tools_are_only_the_read_tools():
    assert assistant.ALLOWED_TOOLS == [
        "mcp__handboek__search_docs",
        "mcp__handboek__read_page",
        "mcp__handboek__list_components",
        "mcp__platform__platform_status",
    ]


def test_builtin_write_and_exec_tools_are_disallowed():
    for tool in ("Bash", "Write", "Edit", "WebFetch", "WebSearch", "Task"):
        assert tool in assistant.DISALLOWED_TOOLS


def test_system_prompt_states_read_only_and_provenance():
    p = assistant.SYSTEM_PROMPT
    assert "read_page" in p and "herkomst" in p
    assert "pull request" in p
    assert "data, geen instructie" in p  # injectie-regel


# --- tool-implementaties tegen een fake store --------------------------------

class _FakePage:
    def __init__(self, component, path, body):
        self.component = component
        self.path = path
        self.body = body
        self.owner = "mark"
        self.last_reviewed = "2026-07-01"
        self.source = f"https://example/{component}/{path}"


class _FakeComp:
    def __init__(self, name):
        self.name = name


class _FakeStore:
    unavailable = {}

    def pages(self, comp):
        return [_FakePage(comp.name, "index.md", "# Titel\ntenant toevoegen")]

    def read_page(self, comp, path):
        if path != "index.md":
            raise FileNotFoundError(path)
        return _FakePage(comp.name, path, "# Titel\ninhoud")


@pytest.fixture
def fake_hub(monkeypatch):
    comps = [_FakeComp("monitoring")]
    monkeypatch.setattr(assistant, "_store", _FakeStore())
    monkeypatch.setattr(assistant, "_components", comps)
    monkeypatch.setattr(assistant, "_hub",
                        lambda: (assistant._store, assistant._components))
    # docs_mcp.search is een hub-import; vervang door een minimale fake
    # zodat deze test zonder hub-checkout draait.
    fake_search = types.ModuleType("docs_mcp.search")

    class _Hit:
        def __init__(self, page):
            self.page = page
            self.score = 5
            self.snippet = page.body[:40]

    fake_search.search = lambda pages, q, limit=10: [
        _Hit(p) for p in pages if q.lower() in p.body.lower()][:limit]
    fake_pkg = types.ModuleType("docs_mcp")
    fake_pkg.search = fake_search
    monkeypatch.setitem(sys.modules, "docs_mcp", fake_pkg)
    monkeypatch.setitem(sys.modules, "docs_mcp.search", fake_search)
    return comps


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_read_page_records_provenance_as_source(fake_hub):
    sources = []
    impls = assistant._tool_impls(sources)
    out = _run(impls["read_page"]({"component": "monitoring",
                                   "path": "index.md"}))
    assert out["owner"] == "mark" and out["last_reviewed"] == "2026-07-01"
    assert sources == [{k: out[k] for k in
                        ("component", "path", "owner", "last_reviewed",
                         "source")}]
    # tweede keer lezen dupliceert de bron niet
    _run(impls["read_page"]({"component": "monitoring", "path": "index.md"}))
    assert len(sources) == 1


def test_search_docs_returns_hits_without_recording_sources(fake_hub):
    sources = []
    impls = assistant._tool_impls(sources)
    hits = _run(impls["search_docs"]({"query": "tenant"}))
    assert hits and hits[0]["component"] == "monitoring"
    assert sources == []  # alleen gelézen pagina's zijn bronnen


def test_unknown_component_is_a_clear_error(fake_hub):
    impls = assistant._tool_impls([])
    with pytest.raises(ValueError, match="onbekende component"):
        _run(impls["read_page"]({"component": "nope", "path": "index.md"}))


# --- platform_status (live status, fase 1 add-assistant-live-status) ---------

def _fake_apps():
    return [
        {"name": "nc-a", "tenant": "a", "sync": "Synced", "health": "Healthy"},
        {"name": "nc-b", "tenant": "b", "sync": "OutOfSync",
         "health": "Degraded"},
        {"name": "openwoo-provisioner", "tenant": "", "sync": "Synced",
         "health": "Progressing"},
    ]


def test_platform_status_summary_counts(fake_hub, monkeypatch):
    import argolib
    monkeypatch.setattr(argolib, "list_apps", lambda prefix="": _fake_apps())
    calls = []
    impls = assistant._tool_impls([], calls)
    out = _run(impls["platform_status"]({}))
    assert out["beschikbaar"] is True and out["totaal"] == 3
    assert out["per_health"] == {"Healthy": 1, "Degraded": 1,
                                 "Progressing": 1}
    assert out["aandacht_aantal"] == 2
    # samenvatting bevat géén lijsten — dat is de compacte weergave
    assert "aandacht" not in out and "applicaties" not in out
    assert out["bron"].startswith("Argo CD") and out["opgehaald"]
    assert calls == [{"tool": "platform_status", "weergave": "samenvatting",
                      "resultaat": "apps=3"}]


def test_platform_status_degraded_view_lists_attention_only(fake_hub,
                                                            monkeypatch):
    import argolib
    monkeypatch.setattr(argolib, "list_apps", lambda prefix="": _fake_apps())
    impls = assistant._tool_impls([], [])
    out = _run(impls["platform_status"]({"weergave": "degraded"}))
    assert [a["name"] for a in out["aandacht"]] == ["nc-b",
                                                    "openwoo-provisioner"]
    assert "applicaties" not in out


def test_platform_status_alles_lists_everything(fake_hub, monkeypatch):
    import argolib
    monkeypatch.setattr(argolib, "list_apps", lambda prefix="": _fake_apps())
    impls = assistant._tool_impls([], [])
    out = _run(impls["platform_status"]({"weergave": "alles"}))
    assert len(out["applicaties"]) == 3 and len(out["aandacht"]) == 2


def test_platform_status_rejects_freeform_view(fake_hub):
    # spec: fixed read-only status surface — geen vrije input
    impls = assistant._tool_impls([], [])
    with pytest.raises(ValueError, match="samenvatting"):
        _run(impls["platform_status"]({"weergave": "sum(rate(x[5m]))"}))


def test_platform_status_backend_unreachable_is_honest(fake_hub, monkeypatch):
    import argolib

    def boom(prefix=""):
        raise argolib.ArgoError(0, "cannot reach kube API")

    monkeypatch.setattr(argolib, "list_apps", boom)
    calls = []
    impls = assistant._tool_impls([], calls)
    out = _run(impls["platform_status"]({"weergave": "samenvatting"}))
    assert out["beschikbaar"] is False and "onbereikbaar" in out["fout"]
    assert calls[0]["resultaat"] == "onbereikbaar"


def test_system_prompt_labels_live_data():
    p = assistant.SYSTEM_PROMPT
    assert "platform_status" in p and "live" in p
    assert "verzin" in p  # backend weg => eerlijk, niet verzinnen


# --- audit (spec: auditable sessions) ----------------------------------------

def test_audit_appends_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(assistant, "AUDIT_LOG_PATH", str(path))
    record = {"user": "a@x", "question": "q", "answer": "a",
              "sources": [], "is_error": False}
    assistant._audit(record)
    assistant._audit(record)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["user"] == "a@x"


# --- benchmark-vragenset (webgui/bench_questions.json) ------------------------

def test_bench_questions_structure():
    data = json.loads((REPO_ROOT / "webgui" /
                       "bench_questions.json").read_text())
    ids = [q["id"] for q in data["vragen"]]
    assert len(ids) == len(set(ids)), "dubbele vraag-id"
    for q in data["vragen"]:
        assert q["vraag"].strip()
        assert q["verwacht"] in ("gegrond", "buiten-handboek", "weigering")
    # de drie spec-scenario's (4.1) zitten in de set
    assert {"buiten-handboek", "weigering"} <= {q["verwacht"]
                                                for q in data["vragen"]}


# Route-tests staan in tests/test_assistant_routes.py (skippen als geheel
# zonder Flask; dit bestand moet ook onder systeem-python blijven draaien).
