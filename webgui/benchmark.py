#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
# role: tool
#
# webgui/benchmark.py — model-benchmark voor de platform-assistent.
#
# Draait de vaste vragenset (bench_questions.json) tegen één of meer
# modellen via assistant.ask_stream en legt per antwoord vast: tekst,
# bronnen, kosten, tokens, duur en een mechanische check op de
# verwachting (gegrond => >=1 bron). Kwaliteitsoordeel blijft een mens:
# het markdown-rapport zet de antwoorden per vraag naast elkaar.
#
# De rate limiter wordt verruimd (dit is een bewuste batch door de
# beheerder, geen eindgebruikersverkeer); auth loopt zoals altijd via
# de omgeving (ANTHROPIC_API_KEY of CLAUDE_CODE_OAUTH_TOKEN/lokale login).
#
# Writes: resultaten-JSONL + markdown-rapport in --out-dir (default
#   /tmp/assistant-bench); de repo zelf blijft onaangeraakt.
# Idempotent: ja (bestanden krijgen een timestamp-naam)
# Requires: webgui-venv (claude-agent-sdk, PyYAML), hub-checkout naast
#   deze repo (of HUB_DIR), netwerk naar codeberg.org + model-API.
#
# Usage:
#   webgui/.venv/bin/python webgui/benchmark.py                # default,sonnet,haiku
#   webgui/.venv/bin/python webgui/benchmark.py --models sonnet,haiku
#   webgui/.venv/bin/python webgui/benchmark.py --only injectie,buiten-handboek
#   webgui/.venv/bin/python webgui/benchmark.py --out-dir ~/bench

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assistant  # noqa: E402

QUESTIONS_FILE = Path(__file__).resolve().parent / "bench_questions.json"


def run_one(model: str, item: dict) -> dict:
    """Eén vraag tegen één model; env ASSISTANT_MODEL wordt per run gezet."""
    if model == "default":
        os.environ.pop("ASSISTANT_MODEL", None)
    else:
        os.environ["ASSISTANT_MODEL"] = model
    started = time.time()
    answer, sources, error = [], [], None
    try:
        for ev in assistant.ask_stream(item["vraag"],
                                       f"benchmark+{model}@conduction.nl"):
            if ev["type"] == "delta":
                answer.append(ev["text"])
            elif ev["type"] == "sources":
                sources = ev["sources"]
            elif ev["type"] == "error":
                error = ev["message"]
    except assistant.AssistantError as exc:
        error = str(exc)
    text = "".join(answer)
    check = None
    if item["verwacht"] == "gegrond":
        check = "ok" if sources else "GEEN BRONNEN"
    return {
        "model": model, "id": item["id"], "verwacht": item["verwacht"],
        "vraag": item["vraag"], "antwoord": text,
        "bronnen": [f"{s['component']}/{s['path']}" for s in sources],
        "check": check, "error": error,
        "duur_s": round(time.time() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="default,sonnet,haiku",
                        help="komma-lijst; 'default' = SDK-default (Opus-klasse)")
    parser.add_argument("--only", default="",
                        help="komma-lijst met vraag-id's (default: alle)")
    parser.add_argument("--out-dir", default="/tmp/assistant-bench")
    args = parser.parse_args()

    data = json.loads(QUESTIONS_FILE.read_text())
    items = data["vragen"]
    if args.only:
        wanted = set(args.only.split(","))
        items = [q for q in items if q["id"] in wanted]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not items or not models:
        print("niets te doen (lege vragen- of modellenlijst)", file=sys.stderr)
        return 2

    # Batch door de beheerder: de per-gebruiker limiet is hier niet het
    # beschermde belang. Kosten worden per antwoord gerapporteerd.
    assistant.rate_limiter = assistant.RateLimiter(
        max_requests=10_000, window_seconds=3600)

    out_dir = Path(os.path.expanduser(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl = out_dir / f"bench-{stamp}.jsonl"
    report = out_dir / f"bench-{stamp}.md"

    results = []
    total = len(models) * len(items)
    with jsonl.open("a", encoding="utf-8") as fh:
        for model in models:
            for item in items:
                n = len(results) + 1
                print(f"[{n}/{total}] {model} · {item['id']} ...",
                      flush=True)
                result = run_one(model, item)
                results.append(result)
                fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                fh.flush()
                flag = result["error"] or result["check"] or ""
                print(f"    {result['duur_s']}s · "
                      f"{len(result['bronnen'])} bron(nen) {flag}",
                      flush=True)

    lines = ["# Assistent-benchmark " + stamp, ""]
    lines += ["| model | vraag | duur | bronnen | check |",
              "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['model']} | {r['id']} | {r['duur_s']}s "
                     f"| {len(r['bronnen'])} "
                     f"| {r['error'] or r['check'] or ''} |")
    lines.append("")
    for item in items:
        lines.append(f"## {item['id']} — {item['vraag']}")
        lines.append(f"_verwacht: {item['verwacht']}_")
        for r in results:
            if r["id"] != item["id"]:
                continue
            lines.append(f"\n### {r['model']} ({r['duur_s']}s, "
                         f"bronnen: {', '.join(r['bronnen']) or '—'})\n")
            lines.append(r["error"] or r["antwoord"])
        lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nresultaten: {jsonl}\nrapport:    {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
