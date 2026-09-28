"""Run the AI accountant eval set (roadmap §5.5) — on a SCRATCH database only.

Seeds the eval books (app/services/ai_eval/fixture.py) in a fresh company,
plays every scenario through the production agent loop and scores it:

    # the configured model (env / app_settings), three runs per scenario,
    # compared with an earlier report; exit 1 on a regression
    DATABASE_URL=postgresql+psycopg://…/aa_eval_scratch APP_ENV=test \\
    python -m scripts.ai_eval run --repeat 3 --previous last.json --out report.json \\
        --summary summary.md --fail-on-regression

    # compare models side by side (one report per model)
    python -m scripts.ai_eval run --models gpt-4.1-mini,gpt-5-mini --out /tmp/eval.json

    # no model at all: the recorded trajectories (what the test suite runs)
    python -m scripts.ai_eval replay

Refuses a database whose name has none of "scratch", "eval" or "bench", and
APP_ENV=prod: the run writes a company, journals and (cancelled) cards.
The nightly job is .github/workflows/ai-eval.yml.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

SAFE_NAME_WORDS = ("scratch", "eval", "bench")


def guard(database_url: str, app_env: str) -> str | None:
    """Why this database must not be used, or None."""
    from sqlalchemy.engine import make_url
    name = (make_url(database_url).database or "").lower()
    if (app_env or "").lower() == "prod":
        return f"refusing to run with APP_ENV={app_env}"
    if not any(w in name for w in SAFE_NAME_WORDS):
        return f"refusing to run against database {name!r}: its name must contain one of {SAFE_NAME_WORDS}"
    return None


def _live_client(db, model: str | None):
    from app.core.ai_runtime import load_ai_config_from_db, resolve_active_ai_backend
    from app.services.ai_accountant.orchestrator import _get_chat_client, _resolve_chat_shape
    from app.services.ai_eval.runner import MeteredClient

    load_ai_config_from_db()
    backend = resolve_active_ai_backend()
    shape = _resolve_chat_shape(db)
    # a local LM Studio needs no key; every hosted provider does
    if shape != "anthropic" and backend.get("provider") != "lmstudio" and not (backend.get("api_key") or "").strip():
        raise SystemExit("no AI key configured (METIS_API_KEY / AI_API_KEY / Settings) — nothing to evaluate")
    chosen = model or backend.get("model") or "configured"
    return (lambda _sc: MeteredClient(_get_chat_client(shape), model=model)), chosen


def _load(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def _run(args) -> int:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.ai_eval.fixture import seed_eval_company
    from app.services.ai_eval.runner import (
        ReplayClient, RunOptions, compare, fill, load_scenarios, markdown, run_suite,
    )

    why = guard(settings.database_url, settings.app_env)
    if why:
        print(why, file=sys.stderr)
        return 2
    scenarios = load_scenarios()
    only = set(args.only.split(",")) if args.only else None
    previous = _load(args.previous)
    db = SessionLocal()
    exit_code = 0
    try:
        ctx = seed_eval_company(db)
        if args.command == "replay":
            plans = [("replay", lambda sc: ReplayClient(fill(sc["replay"], ctx)))]
        else:
            models = args.models.split(",") if args.models else [args.model]
            plans = []
            for m in models:
                client_for, name = _live_client(db, m)
                plans.append((name, client_for))
        reports = []
        for name, client_for in plans:
            opts = RunOptions(model=name, repeat=1 if args.command == "replay" else args.repeat, only=only,
                              replay=args.command == "replay")
            print(f"\n== {name} ==")
            report = await run_suite(db, ctx, scenarios, client_for, opts, log=print)
            regressions = compare(report, previous)
            report["regressions"] = regressions
            reports.append(report)
            print(f"\n{name}: {report['passed']}/{report['total']} passed ({report['pass_rate']:.0%}), "
                  f"{report['input_tokens']:,}/{report['output_tokens']:,} tokens"
                  + (f", ${report['usd']:.4f}" if report.get("usd") is not None else ""))
            for r in regressions:
                print(f"  REGRESSION {r}")
            if regressions and args.fail_on_regression:
                exit_code = 1
            if args.command == "replay" and report["passed"] < report["total"]:
                exit_code = 1
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            if len(reports) == 1:
                out.write_text(json.dumps(reports[0], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            else:
                for rep in reports:
                    p = out.with_name(f"{out.stem}-{rep['model']}{out.suffix}")
                    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as fh:
                for rep in reports:
                    fh.write(markdown(rep, rep["regressions"], previous) + "\n")
        if len(reports) > 1:
            print("\n| model | passed | tokens in/out | USD |\n|---|---|---|---|")
            for rep in reports:
                usd = f"${rep['usd']:.4f}" if rep.get("usd") is not None else "—"
                print(f"| {rep['model']} | {rep['passed']}/{rep['total']} | "
                      f"{rep['input_tokens']:,}/{rep['output_tokens']:,} | {usd} |")
    finally:
        db.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["run", "replay"])
    ap.add_argument("--model", help="model to evaluate (default: the configured one)")
    ap.add_argument("--models", help="comma-separated models, one report each")
    ap.add_argument("--repeat", type=int, default=3, help="runs per scenario (models vary; default 3)")
    ap.add_argument("--only", help="comma-separated scenario ids")
    ap.add_argument("--out", help="write the report (JSON) here")
    ap.add_argument("--previous", help="an earlier report to compare with")
    ap.add_argument("--summary", help="append a markdown summary here (e.g. $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--fail-on-regression", action="store_true")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
