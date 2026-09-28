"""OCR model bench on OUR bank statements, against Metis pricing.

  python scripts/model_eval.py ocr --pdf /tmp/mellat.pdf [--models gemini-2.5-pro,gemini-2.5-flash,...]

Rasterise the statement, ask each vision model for rows with the production
prompt, compare with the first model (baseline) row by row (date, amount,
direction, balance), record latency and token usage → cost.

The chat bench that lived here is now the eval set (roadmap §5.5):
``scripts/ai_eval.py`` — the scenarios, fixture books and scoring are in
``app/services/ai_eval/``; ``--models a,b,c`` compares models side by side.
Prices (USD per 1M tokens) are in ``app/services/ai_eval/runner.py``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from app.services.ai_eval.runner import PRICES, cost as _cost  # noqa: E402,F401 — one price table


def cost(model: str, inp: int, out: int) -> float:
    c = _cost(model, inp, out)
    return float("nan") if c is None else c


# ---------------------------------------------------------------------------
# OCR bench
# ---------------------------------------------------------------------------

async def ocr_bench(pdf: str, models: list[str], out_path: Path) -> None:
    import httpx
    from app.core.ai_runtime import load_ai_config_from_db, resolve_active_ai_backend
    from app.core.config import settings
    from app.services.ocr_extract import _STATEMENT_PROMPT, _parse_json_array, _rasterize_pages, _normalize_date, coerce_amount

    load_ai_config_from_db()
    key = (resolve_active_ai_backend().get("api_key") or "").strip()
    base = settings.gemini_base_url.rstrip("/")
    pages = _rasterize_pages(Path(pdf), "application/pdf")
    print(f"pages rasterised: {len(pages)}")

    results = {}
    baseline_rows = None
    for model in models:
        url = f"{base}/models/{model}:generateContent"
        parts = [{"text": _STATEMENT_PROMPT}] + [{"inline_data": {"mime_type": m, "data": b}} for m, b in pages]
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.post(url, json={"contents": [{"parts": parts}], "generationConfig": {"temperature": 0}},
                                      headers={"x-goog-api-key": key})
            secs = time.time() - t0
            if r.status_code != 200:
                results[model] = {"error": f"HTTP {r.status_code}: {r.text[:200]}", "secs": round(secs, 1)}
                print(f"{model}: HTTP {r.status_code} {r.text[:120]}")
                continue
            body = r.json()
            usage = body.get("usageMetadata") or {}
            text = "".join(p.get("text", "") for p in ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
            raw = _parse_json_array(text)
        except Exception as e:  # noqa: BLE001
            results[model] = {"error": f"{type(e).__name__}: {e}"[:200], "secs": round(time.time() - t0, 1)}
            print(f"{model}: {type(e).__name__}: {e}"[:160])
            continue
        rows = []
        for x in raw:
            amt = coerce_amount(x.get("amount"))
            if amt is None or amt <= 0:
                continue
            rows.append({"date": _normalize_date(x.get("date")), "amount": amt,
                         "direction": str(x.get("direction") or "").lower(),
                         "balance": coerce_amount(x.get("balance")), "description": str(x.get("description") or "")})
        inp, out = int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
        rec = {"rows": len(rows), "secs": round(secs, 1), "input_tokens": inp, "output_tokens": out,
               "usd": round(cost(model, inp, out), 4), "data": rows}
        if baseline_rows is None:
            baseline_rows = rows
            rec["vs_baseline"] = "baseline"
        else:
            key_of = lambda r: (r["date"], r["amount"], r["direction"])  # noqa: E731
            base_keys = {key_of(r) for r in baseline_rows}
            mine = {key_of(r) for r in rows}
            bal_match = sum(1 for r in rows if any(b["date"] == r["date"] and b["amount"] == r["amount"] and b["balance"] == r["balance"] for b in baseline_rows))
            rec["vs_baseline"] = {"rows_matching": len(base_keys & mine), "missing": len(base_keys - mine),
                                  "extra": len(mine - base_keys), "balance_matching": bal_match}
        results[model] = rec
        print(f"{model}: rows={rec['rows']} secs={rec['secs']} in={inp} out={out} usd={rec['usd']} vs_baseline={rec['vs_baseline']}")
    out_path.write_text(json.dumps({"pdf": pdf, "models": results, "at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=1, default=str))
    print("\n| model | rows | vs baseline (match/missing/extra, balances) | secs | in/out tokens | USD per statement |")
    print("|---|---|---|---|---|---|")
    for m, r in results.items():
        if "error" in r:
            print(f"| {m} | — | {r['error'][:60]} | {r['secs']} | — | — |")
            continue
        vb = r["vs_baseline"]
        vbs = "baseline" if vb == "baseline" else f"{vb['rows_matching']}/{vb['missing']}/{vb['extra']}, bal {vb['balance_matching']}"
        print(f"| {m} | {r['rows']} | {vbs} | {r['secs']} | {r['input_tokens']}/{r['output_tokens']} | ${r['usd']:.4f} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("bench", choices=["ocr", "chat"])
    ap.add_argument("--pdf")
    ap.add_argument("--models")
    ap.add_argument("--out", default="/tmp/model_eval.json")
    a, _rest = ap.parse_known_args()
    if a.bench == "chat":
        sys.exit("the chat bench is now the eval set: python -m scripts.ai_eval run --models " + (a.models or "…"))
    models = (a.models or "gemini-2.5-pro,gemini-2.5-flash,gemini-2.5-flash-lite,gemini-3-flash-preview,gemini-3.1-flash-lite").split(",")
    asyncio.run(ocr_bench(a.pdf, models, Path(a.out)))


if __name__ == "__main__":
    sys.exit(main())
