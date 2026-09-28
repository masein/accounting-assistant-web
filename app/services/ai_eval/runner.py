"""Play eval scenarios through the production agent loop and compare runs."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.ai_eval.fixture import EvalContext
from app.services.ai_eval.scoring import score

SCENARIOS_PATH = Path(__file__).with_name("scenarios.json")

# (input, output) USD per 1M tokens — docs.metisai.ir/pricing, 2026-09-24
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.165, 0.66), "gpt-4o": (2.75, 11.0),
    "gpt-4.1": (2.2, 8.8), "gpt-4.1-mini": (0.44, 1.76), "gpt-4.1-nano": (0.11, 0.44),
    "gpt-5": (1.375, 11.0), "gpt-5-mini": (0.275, 2.2), "gpt-5-nano": (0.055, 0.44),
    "gpt-5.4-mini": (0.8625, 5.175), "gpt-5.4-nano": (0.23, 1.4375),
    "gpt-5.6-luna": (0.22, 1.32), "gpt-5.6-terra": (2.2, 13.2),
    "gemini-2.5-pro": (2.75, 16.5), "gemini-2.5-flash": (0.33, 2.75), "gemini-2.5-flash-lite": (0.11, 0.44),
    "gemini-3-flash-preview": (0.55, 3.3), "gemini-3.1-flash-lite": (0.275, 1.65),
    "gemini-3.6-flash": (0.825, 4.125), "gemini-3.7-flash": (0.825, 4.125), "gemini-3.5-flash": (1.65, 9.9),
    "gemini-3.5-flash-lite": (0.33, 2.75),
}


def cost(model: str, inp: int, out: int) -> float | None:
    p = PRICES.get(model)
    return None if not p else round((inp * p[0] + out * p[1]) / 1_000_000, 6)


def load_scenarios(path: Path | None = None) -> list[dict]:
    data = json.loads((path or SCENARIOS_PATH).read_text(encoding="utf-8"))
    return data["scenarios"]


def fill(value: Any, ctx: EvalContext, marks: dict[str, str] | None = None) -> Any:
    """Replace ``{placeholder}`` strings (whole values or inside text) from the fixture."""
    marks = ctx.placeholders() if marks is None else marks
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}") and value[1:-1] in marks:
            return marks[value[1:-1]]
        for k, v in marks.items():
            if "{" + k + "}" in value:
                value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [fill(v, ctx, marks) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, ctx, marks) for k, v in value.items()}
    return value


# --- the replay model -------------------------------------------------------------------------------

def _collect_errors(client, messages) -> None:
    """Tool failures reach the model as error results; keep their text."""
    for m in messages:
        if getattr(m, "role", None) == "tool" and getattr(m, "is_error", False):
            key = getattr(m, "tool_call_id", None)
            if key not in client.error_ids:
                client.error_ids.add(key)
                client.errors.append((m.text or "")[:300])


class ReplayClient:
    """Plays a scenario's recorded trajectory: each ``{"tool", "input"}`` step
    becomes one assistant turn calling that tool, then ``{"text"}`` ends the
    turn. It ignores what the tools answer — the point is that today's tools
    still accept the recorded calls and make the cards the scenario expects."""

    shape = "replay"

    def __init__(self, steps: list[dict]) -> None:
        self.steps = list(steps)
        self.calls = self.input_tokens = self.output_tokens = 0
        self.errors: list[str] = []
        self.error_ids: set = set()

    async def chat(self, *, system_prompt, tools, messages, model=None, max_tokens=8192):
        from app.services.ai_accountant.llm_protocol import ChatMessage, LLMResponse, LLMUsage, ToolCall
        self.calls += 1
        _collect_errors(self, messages)
        known = {t["name"] for t in tools}
        if not self.steps:
            return LLMResponse(message=ChatMessage(role="assistant", text="(replay: no more steps)"),
                               stop_reason="end_turn", usage=LLMUsage(input_tokens=0, output_tokens=0))
        step = self.steps.pop(0)
        if "text" in step:
            return LLMResponse(message=ChatMessage(role="assistant", text=step["text"]),
                               stop_reason="end_turn", usage=LLMUsage(input_tokens=0, output_tokens=0))
        if step["tool"] not in known:
            raise AssertionError(f"replay calls {step['tool']!r}, which the agent no longer offers")
        call = ToolCall(id=f"replay_{uuid.uuid4().hex[:8]}", name=step["tool"], input=step.get("input") or {})
        return LLMResponse(message=ChatMessage(role="assistant", text=None, tool_calls=[call]),
                           stop_reason="tool_use", usage=LLMUsage(input_tokens=0, output_tokens=0))


class MeteredClient:
    """Wraps a live client: forces a model (optional) and counts tokens."""

    def __init__(self, inner, model: str | None = None) -> None:
        self.inner, self.model = inner, model
        self.shape = getattr(inner, "shape", "live")
        self.calls = self.input_tokens = self.output_tokens = 0
        self.errors: list[str] = []
        self.error_ids: set = set()

    async def chat(self, **kw):
        _collect_errors(self, kw.get("messages") or [])
        if self.model:
            kw["model"] = self.model
        resp = await self.inner.chat(**kw)
        self.calls += 1
        self.input_tokens += int(getattr(resp.usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(resp.usage, "output_tokens", 0) or 0)
        return resp


# --- one scenario ------------------------------------------------------------------------------------

async def run_scenario(db: Session, ctx: EvalContext, scenario: dict, client, *, replay: bool = False) -> dict:
    """One turn of one scenario inside the eval company. Every card it makes
    is cancelled before returning, so nothing can be confirmed later."""
    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.orchestrator import run_chat_turn

    expect = dict(fill(scenario.get("expect") or {}, ctx))
    if replay:
        expect["max_tool_errors"] = 0
    t0 = time.perf_counter()
    rec: dict[str, Any] = {"scenario": scenario["id"]}
    try:
        with use_company(ctx.company_id):
            res = await run_chat_turn(db, user_id=ctx.user_id, username="ai-eval",
                                      user_message=fill(scenario["message"], ctx), session_id=None,
                                      lang=scenario.get("lang", "en"), client=client)
            tokens = [uuid.UUID(p["confirmation_token"]) for p in res.proposals or []]
            rows = {r.confirmation_token: r for r in db.execute(
                select(AIProposal).where(AIProposal.confirmation_token.in_(tokens))).scalars()} if tokens else {}
            cards = []
            for tok in tokens:
                row = rows.get(tok)
                if row is not None:
                    cards.append({"tool": row.tool_name, "input": dict(row.tool_input or {})})
                    row.status = "cancelled"
            db.commit()
        tools = [c.get("name") for c in res.tool_calls or []]
        errors = sum(1 for c in res.tool_calls or [] if "result" not in c)
        problems = score(expect, tools=tools, tool_errors=errors, cards=cards, reply=res.text or "",
                         today=ctx.today)
        rec.update(ok=not problems, problems=problems, tools=tools, tool_errors=errors,
                   cards=[{"tool": c["tool"], "total": _total(c)} for c in cards],
                   reply=(res.text or "")[:400], turns=res.turns, stop_reason=res.stop_reason)
    except Exception as e:  # noqa: BLE001 — a crash is a failed scenario, not a failed run
        db.rollback()
        rec.update(ok=False, problems=[f"{type(e).__name__}: {e}"[:300]], tools=[], tool_errors=0, cards=[],
                   reply="", turns=0, stop_reason="error")
    rec["tool_error_texts"] = list(getattr(client, "errors", []))[:5]
    if rec["tool_error_texts"] and not rec["ok"]:
        rec["problems"] = rec["problems"] + [f"tool said: {rec['tool_error_texts'][0][:160]}"]
    rec.update(secs=round(time.perf_counter() - t0, 2), calls=getattr(client, "calls", 0),
               input_tokens=getattr(client, "input_tokens", 0), output_tokens=getattr(client, "output_tokens", 0))
    return rec


def _total(card: dict) -> int:
    from app.services.ai_eval.scoring import card_total
    return card_total(card["tool"], card["input"])


# --- a run ---------------------------------------------------------------------------------------------

@dataclass
class RunOptions:
    model: str = "replay"
    repeat: int = 1
    only: set[str] | None = None
    replay: bool = False


async def run_suite(db: Session, ctx: EvalContext, scenarios: list[dict], client_for: Callable[[dict], Any],
                    opts: RunOptions, *, log: Callable[[str], None] | None = None) -> dict:
    """Every scenario ``opts.repeat`` times; ``client_for(scenario)`` makes a
    fresh client per turn. Returns the report (see ``summarise``)."""
    runs: dict[str, list[dict]] = {}
    for sc in scenarios:
        if opts.only and sc["id"] not in opts.only:
            continue
        for _ in range(max(1, opts.repeat)):
            rec = await run_scenario(db, ctx, sc, client_for(sc), replay=opts.replay)
            runs.setdefault(sc["id"], []).append(rec)
            if log:
                log(f"{sc['id']:28s} {'PASS' if rec['ok'] else 'FAIL'} {rec['secs']:6.1f}s "
                    f"{','.join(rec['tools'])[:70]:70s} {'; '.join(rec['problems'])[:140]}")
    return summarise(runs, scenarios, opts)


def summarise(runs: dict[str, list[dict]], scenarios: list[dict], opts: RunOptions) -> dict:
    critical = {s["id"] for s in scenarios if s.get("critical")}
    per: dict[str, dict] = {}
    for sid, recs in runs.items():
        passed = sum(1 for r in recs if r["ok"])
        per[sid] = {"passed": passed, "total": len(recs), "rate": round(passed / len(recs), 3),
                    "critical": sid in critical, "runs": recs}
    tin = sum(r["input_tokens"] for recs in runs.values() for r in recs)
    tout = sum(r["output_tokens"] for recs in runs.values() for r in recs)
    total = sum(v["total"] for v in per.values())
    passed = sum(v["passed"] for v in per.values())
    return {
        "model": opts.model, "repeat": opts.repeat, "replay": opts.replay,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "passed": passed, "total": total, "pass_rate": round(passed / total, 3) if total else 0.0,
        "input_tokens": tin, "output_tokens": tout, "usd": cost(opts.model, tin, tout),
        "secs": round(sum(r["secs"] for recs in runs.values() for r in recs), 1),
        "scenarios": per,
    }


# --- comparing runs ------------------------------------------------------------------------------------

STABLE = 2 / 3          # a scenario that passed at least this often before…
BROKEN = 0.5            # …and now passes less often than this has regressed
OVERALL_DROP = 0.10     # or the whole run's pass rate fell by this much


def compare(current: dict, previous: dict | None) -> list[str]:
    """Regressions of ``current`` against an earlier run (empty = none).

    A critical scenario (a refusal, say) regresses the moment any run fails.
    Other scenarios are models: one stray miss out of three isn't news, so a
    scenario regresses only if it passed reliably before (≥ 2/3) and now
    passes less than half the time. The pass rate over the scenarios both runs
    share falling by ten points is a regression too. Scenarios new since
    ``previous`` can't regress.
    """
    out: list[str] = []
    for sid, cur in current["scenarios"].items():
        if cur["critical"] and cur["passed"] < cur["total"]:
            out.append(f"{sid}: critical scenario failed {cur['total'] - cur['passed']}/{cur['total']} "
                       f"({'; '.join(_first_problems(cur))})")
            continue
        prev = (previous or {}).get("scenarios", {}).get(sid)
        if prev and prev["rate"] >= STABLE and cur["rate"] < BROKEN:
            out.append(f"{sid}: passed {prev['passed']}/{prev['total']} before, {cur['passed']}/{cur['total']} now "
                       f"({'; '.join(_first_problems(cur))})")
    # overall, over the scenarios both runs have (a new, hard scenario isn't a drop)
    both = set(current["scenarios"]) & set((previous or {}).get("scenarios", {}))
    if both:
        def rate(rep):
            n = sum(rep["scenarios"][s]["total"] for s in both)
            return sum(rep["scenarios"][s]["passed"] for s in both) / n if n else 0.0
        was, now = rate(previous), rate(current)
        if now <= was - OVERALL_DROP:
            out.append(f"overall pass rate {was:.0%} → {now:.0%}")
    return out


def _first_problems(entry: dict) -> list[str]:
    """The distinct first problem of each failed run, at most two."""
    seen: list[str] = []
    for r in entry["runs"]:
        if not r["ok"] and r["problems"] and r["problems"][0] not in seen:
            seen.append(r["problems"][0])
    return seen[:2]


def markdown(report: dict, regressions: list[str], previous: dict | None = None) -> str:
    """A job-summary table: one row per scenario, with the previous run's rate."""
    usd = f"${report['usd']:.4f}" if report.get("usd") is not None else "—"
    lines = [f"### AI eval — {report['model']}: {report['passed']}/{report['total']} "
             f"({report['pass_rate']:.0%}), {report['input_tokens']:,}/{report['output_tokens']:,} tokens, {usd}",
             ""]
    if regressions:
        lines += ["**Regressions**", ""] + [f"- {r}" for r in regressions] + [""]
    lines += ["| scenario | passed | before | problems |", "|---|---|---|---|"]
    prev = (previous or {}).get("scenarios", {})
    for sid, s in report["scenarios"].items():
        before = f"{prev[sid]['passed']}/{prev[sid]['total']}" if sid in prev else "—"
        probs = "; ".join(_first_problems(s)).replace("|", "\\|")[:160]
        lines.append(f"| {sid}{' ⚑' if s['critical'] else ''} | {s['passed']}/{s['total']} | {before} | {probs} |")
    return "\n".join(lines) + "\n"
