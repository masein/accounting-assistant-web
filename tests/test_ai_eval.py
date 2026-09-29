"""The AI accountant's eval set (roadmap §5.5).

Every scenario's recorded trajectory is replayed through today's agent loop —
real tools, real cards, no model, no network — and must still score as a
pass; the scorer, the run comparison and the nightly workflow are pinned too.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.ai_eval.fixture import seed_eval_company
from app.services.ai_eval.runner import ReplayClient, fill, load_scenarios, run_scenario

SCENARIOS = load_scenarios()


@pytest.fixture()
def books(db):
    ctx = seed_eval_company(db)
    yield ctx
    from tests.test_admin_audit import _purge_company
    _purge_company(db, ctx.company_id)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_every_recorded_trajectory_still_passes(db, books, scenario):
    client = ReplayClient(fill(scenario["replay"], books))
    rec = asyncio.run(run_scenario(db, books, scenario, client, replay=True))
    assert rec["ok"], (rec["problems"], rec["tools"], rec["reply"])


# --- the eval set itself ------------------------------------------------------------------------------

_EXPECT_KEYS = {"tools_any", "tools_all", "tools_none", "trajectory", "proposals", "proposals_min", "proposals_max",
                "card", "reply_lang", "reply_contains_any", "reply_numbers_any", "max_tool_errors"}
_CARD_KEYS = {"tool", "total", "debit_prefix", "credit_prefix", "fields", "fields_contain", "bank_statement_row", "date"}


def test_the_eval_set_is_well_formed():
    from app.services.ai_accountant.orchestrator import build_default_registry
    tools = {t["name"] for t in build_default_registry().to_anthropic()}
    ids = [s["id"] for s in SCENARIOS]
    assert len(ids) == len(set(ids)) and len(ids) >= 15
    langs = [s.get("lang", "en") for s in SCENARIOS]
    assert set(langs) == {"en", "fa"} and langs.count("fa") >= 6          # fa and en both, fa well covered
    assert any(s.get("critical") for s in SCENARIOS)                       # refusals can't slide
    for s in SCENARIOS:
        assert set(s["expect"]) <= _EXPECT_KEYS, s["id"]
        assert set(s["expect"].get("card") or {}) <= _CARD_KEYS, s["id"]
        named = {t for k in ("tools_any", "tools_all", "tools_none", "trajectory") for t in s["expect"].get(k, [])}
        named |= {s["expect"]["card"]["tool"]} if s["expect"].get("card", {}).get("tool") else set()
        assert named <= tools, (s["id"], named - tools)                   # no stale tool names
        assert s["replay"] and "text" in s["replay"][-1], s["id"]         # a trajectory ends with the reply
        assert all("tool" in st or "text" in st for st in s["replay"]), s["id"]


def test_replay_refuses_a_tool_the_agent_no_longer_has(db, books):
    sc = {"id": "x", "message": "hi", "expect": {}, "replay": [{"tool": "propose_teleport", "input": {}}, {"text": "."}]}
    rec = asyncio.run(run_scenario(db, books, sc, ReplayClient(sc["replay"]), replay=True))
    assert not rec["ok"] and "no longer offers" in rec["problems"][0]


def test_every_card_the_eval_makes_is_cancelled(db, books):
    from sqlalchemy import select

    from app.db.tenant import use_company
    from app.models.ai_accountant import AIProposal
    sc = next(s for s in SCENARIOS if s["id"] == "expense_en")
    asyncio.run(run_scenario(db, books, sc, ReplayClient(fill(sc["replay"], books)), replay=True))
    with use_company(books.company_id):
        statuses = {p.status for p in db.execute(select(AIProposal).where(AIProposal.user_id == books.user_id)).scalars()}
    assert statuses == {"cancelled"}


def test_the_fixture_figures_are_what_the_scenarios_say(db, books):
    from app.db.tenant import use_company
    from app.services.ai_eval.fixture import CASH_ON_HAND
    from app.services.cash_service import cash_on_hand
    with use_company(books.company_id):
        assert cash_on_hand(db, locale="ir", currency="IRR") == CASH_ON_HAND == 2_070_000_000
    cash_q = next(s for s in SCENARIOS if s["id"] == "cash_question_en")
    assert CASH_ON_HAND in cash_q["expect"]["reply_numbers_any"]
    # Sara's pay run is the PAY-1 wages entry
    from sqlalchemy import select

    from app.models.pay_run import PayRun
    from app.services.ai_eval.fixture import SARA_PAY
    with use_company(books.company_id):
        (run,) = db.execute(select(PayRun)).scalars().all()
        assert (run.status, run.total_net, run.lines[0].employee_name) == ("paid", SARA_PAY, "Sara Ahmadi")
    pay_q = next(s for s in SCENARIOS if s["id"] == "payroll_question_en")
    assert SARA_PAY in pay_q["expect"]["reply_numbers_any"]


# --- scoring ------------------------------------------------------------------------------------------

def _score(expect, *, tools=(), errors=0, cards=(), reply=""):
    from datetime import date

    from app.services.ai_eval.scoring import score
    return score(expect, tools=list(tools), tool_errors=errors, cards=list(cards), reply=reply, today=date(2026, 9, 28))


TXN = {"tool": "propose_create_transaction", "input": {"date": "2026-09-28", "lines": [
    {"account_code": "6112", "debit": 200_000, "credit": 0}, {"account_code": "1110", "debit": 0, "credit": 200_000}]}}


def test_scoring_tools_and_trajectory():
    assert _score({"tools_any": ["a", "b"]}, tools=["b"]) == []
    assert _score({"tools_any": ["a"]}, tools=["b"])
    assert _score({"tools_all": ["a", "b"]}, tools=["a"])
    assert _score({"tools_none": ["x"]}, tools=["a", "x"])
    assert _score({"trajectory": ["a", "c"]}, tools=["a", "b", "c"]) == []
    assert _score({"trajectory": ["c", "a"]}, tools=["a", "b", "c"])
    assert _score({"max_tool_errors": 0}, errors=1)


def test_scoring_cards():
    assert _score({"proposals": 1, "card": {"tool": "propose_create_transaction", "total": 200_000,
                                            "debit_prefix": ["6"], "credit_prefix": ["111"], "date": "today"}},
                  cards=[TXN]) == []
    assert _score({"proposals": 0}, cards=[TXN])
    assert _score({"proposals_max": 0}, cards=[TXN]) and _score({"proposals_min": 2}, cards=[TXN])
    assert _score({"card": {"total": 2_000_000}}, cards=[TXN])                 # the toman ×10 slip
    assert _score({"card": {"debit_prefix": ["111"]}}, cards=[TXN])            # money went the wrong way
    assert _score({"card": {"date": "today-1"}}, cards=[TXN])
    assert _score({"card": {"bank_statement_row": True}}, cards=[TXN])
    assert _score({"card": {"tool": "propose_create_invoice"}}, cards=[TXN]) == ["no propose_create_invoice card"]
    inv = {"tool": "propose_create_invoice", "input": {"party": "Aria Trading",
                                                       "lines": [{"quantity": 2, "unit_price": 25_000_000}]}}
    assert _score({"card": {"tool": "propose_create_invoice", "total": 50_000_000,
                            "fields_contain": {"party": "aria"}}}, cards=[inv]) == []
    ent = {"tool": "propose_create_entity", "input": {"name": "Acme Trading", "type": "supplier"}}
    assert _score({"card": {"fields": {"type": "client"}}}, cards=[ent])


def test_scoring_the_reply():
    from app.services.ai_eval.scoring import numbers_in, reply_language
    assert reply_language("موجودی نقد INV-1001 ۲٬۰۷۰٬۰۰۰٬۰۰۰ ریال است") == "fa"
    assert reply_language("Your cash is 2,070,000,000 IRR") == "en"
    assert {2_070_000_000, 12_000_000} <= numbers_in("۲٬۰۷۰٬۰۰۰٬۰۰۰ and 12,000,000 and 2 070 000 000")
    assert _score({"reply_numbers_any": [2_070_000_000]}, reply="you have ۲٬۰۷۰٬۰۰۰٬۰۰۰ ریال") == []
    assert _score({"reply_numbers_any": [2_070_000_000]}, reply="you have 2,700,000,000")   # a wrong figure
    assert _score({"reply_lang": "fa"}, reply="Your balance is fine")
    assert _score({"reply_contains_any": ["Delta Supplies"]}, reply="Only delta supplies.") == []


# --- comparing runs -----------------------------------------------------------------------------------

def _report(rates: dict[str, tuple[int, int]], critical=()):
    from app.services.ai_eval.runner import RunOptions, summarise
    runs = {sid: [{"ok": i < p, "problems": [] if i < p else ["nope"], "secs": 1.0, "input_tokens": 10,
                   "output_tokens": 5} for i in range(n)] for sid, (p, n) in rates.items()}
    scen = [{"id": sid, "critical": sid in critical} for sid in rates]
    return summarise(runs, scen, RunOptions(model="gpt-4.1-mini", repeat=3))


def test_regressions_are_scenarios_that_broke_not_noise():
    from app.services.ai_eval.runner import compare
    steady = {f"s{i}": (3, 3) for i in range(13)}                          # the rest of a 17-scenario set
    before = _report({"a": (3, 3), "b": (2, 3), "c": (1, 3), "d": (3, 3), **steady})
    assert compare(_report({"a": (2, 3), "b": (2, 3), "c": (0, 3), "d": (3, 3), **steady}), before) == []  # stray misses
    got = compare(_report({"a": (1, 3), "b": (0, 3), "c": (0, 3), "d": (3, 3), **steady}), before)
    assert [g.split(":")[0] for g in got[:2]] == ["a", "b"] and not any(g.startswith("c:") for g in got)
    assert not any("overall pass rate" in g for g in got)                   # 4 of 51 runs is not a collapse
    collapsed = _report({"a": (0, 3), "b": (0, 3), "c": (0, 3), "d": (0, 3),
                         **{f"s{i}": (1, 3) if i < 3 else (3, 3) for i in range(13)}})
    assert any("overall pass rate" in g for g in compare(collapsed, before))
    assert compare(_report({"new": (0, 3)}), before) == []                  # nothing to regress from
    assert compare(before, None) == []                                      # the first run


def test_a_critical_scenario_regresses_on_any_failure():
    from app.services.ai_eval.runner import compare
    assert compare(_report({"refuse": (2, 3)}, critical={"refuse"}), None)
    assert compare(_report({"refuse": (3, 3)}, critical={"refuse"}), None) == []


def test_the_summary_is_a_readable_table():
    from app.services.ai_eval.runner import compare, markdown
    before = _report({"a": (3, 3), "refuse": (3, 3)}, critical={"refuse"})
    now = _report({"a": (0, 3), "refuse": (3, 3)}, critical={"refuse"})
    md = markdown(now, compare(now, before), before)
    assert "**Regressions**" in md and "| a | 0/3 | 3/3 | nope |" in md and "| refuse ⚑ | 3/3 | 3/3 |" in md
    assert md.startswith("### AI eval — gpt-4.1-mini: 3/6 (50%)")
    assert now["usd"] == round((60 * 0.44 + 30 * 1.76) / 1_000_000, 6)     # priced from the Metis table


# --- the CLI and the nightly job ----------------------------------------------------------------------

def test_the_cli_refuses_a_real_database():
    import importlib
    cli = importlib.import_module("scripts.ai_eval")
    assert cli.guard("postgresql+psycopg://u:p@db:5432/accounting", "test")
    assert cli.guard("postgresql+psycopg://u:p@db:5432/aa_eval_scratch", "prod")
    assert cli.guard("postgresql+psycopg://u:p@db:5432/aa_eval_scratch", "test") is None


@pytest.fixture()
def cli_on_test_db(db, monkeypatch):
    """The CLI's own session and URL pointed at the test database."""
    import importlib

    import app.db.session as session_mod
    from app.core.config import settings
    from tests.conftest import _TestSession
    monkeypatch.setattr(session_mod, "SessionLocal", _TestSession)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://u:p@localhost:5432/aa_eval_scratch")
    yield importlib.import_module("scripts.ai_eval")
    from sqlalchemy import select

    from app.db.tenant import tenant_bypass
    from app.models.company import Company
    from tests.test_admin_audit import _purge_company
    with tenant_bypass():
        ids = [str(c) for c in db.execute(select(Company.id).where(Company.name == "AI eval books")).scalars()]
    for cid in ids:
        _purge_company(db, cid)


def test_the_cli_replays_the_set_and_writes_a_report(cli_on_test_db, tmp_path):
    import json
    out, summary = tmp_path / "report.json", tmp_path / "summary.md"
    assert cli_on_test_db.main(["replay", "--out", str(out), "--summary", str(summary)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["replay"] and rep["passed"] == rep["total"] == len(SCENARIOS)
    assert summary.read_text(encoding="utf-8").startswith("### AI eval — replay:")


def test_the_cli_fails_on_a_regression(cli_on_test_db, tmp_path, monkeypatch):
    """A 'live' model that only ever says hello, against a previous run that passed everything."""
    import json

    from app.services.ai_eval.runner import ReplayClient
    prev = tmp_path / "prev.json"
    assert cli_on_test_db.main(["replay", "--out", str(prev)]) == 0
    monkeypatch.setattr(cli_on_test_db, "_live_client",
                        lambda db, model: ((lambda sc: ReplayClient([{"text": "hello"}])), "lazy-model"))
    out, summary = tmp_path / "now.json", tmp_path / "s.md"
    code = cli_on_test_db.main(["run", "--repeat", "1", "--previous", str(prev), "--out", str(out),
                                "--summary", str(summary), "--fail-on-regression"])
    assert code == 1
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["model"] == "lazy-model" and rep["regressions"]
    assert any(r.startswith("refusal_structuring") is False for r in rep["regressions"])  # refusals still pass
    assert "**Regressions**" in summary.read_text(encoding="utf-8")
    # without --fail-on-regression the run reports but doesn't fail
    assert cli_on_test_db.main(["run", "--repeat", "1", "--previous", str(prev)]) == 0


def test_the_cli_says_so_without_a_key(cli_on_test_db, monkeypatch):
    from app.core import ai_runtime
    monkeypatch.setattr(ai_runtime, "load_ai_config_from_db", lambda: None)
    monkeypatch.setattr(ai_runtime, "resolve_active_ai_backend",
                        lambda: {"provider": "metis", "api_key": "", "model": "gpt-4.1-mini"})
    import app.services.ai_accountant.orchestrator as orch
    monkeypatch.setattr(orch, "_resolve_chat_shape", lambda db: "openai")
    with pytest.raises(SystemExit) as e:
        cli_on_test_db.main(["run", "--repeat", "1"])
    assert "no AI key" in str(e.value)
    # a local LM Studio has no key and needs none
    monkeypatch.setattr(ai_runtime, "resolve_active_ai_backend",
                        lambda: {"provider": "lmstudio", "api_key": "", "model": "local-model"})
    client_for, name = cli_on_test_db._live_client(None, None)
    assert name == "local-model" and callable(client_for)


def test_the_nightly_job_is_gated_on_its_secret_and_keeps_the_last_good_run():
    import yaml
    wf = yaml.safe_load(open(".github/workflows/ai-eval.yml", encoding="utf-8"))
    on = wf.get("on") or wf.get(True)                           # YAML reads a bare `on:` as True
    assert "schedule" in on and "workflow_dispatch" in on
    job = wf["jobs"]["eval"]
    assert job["env"]["METIS_API_KEY"] == "${{ secrets.AI_EVAL_API_KEY }}"
    assert "aa_eval_scratch" in job["env"]["DATABASE_URL"]      # passes the CLI's scratch guard
    steps = {s["name"]: s for s in job["steps"]}
    assert "exit 0" not in steps["Need an eval key"]["run"] and 'run=false' in steps["Need an eval key"]["run"]
    assert all(s.get("if", "").find("steps.key.outputs.run == 'true'") >= 0 for n, s in steps.items() if n != "Need an eval key")
    run = steps["Run the eval"]["run"]
    assert "--fail-on-regression" in run and "--previous ai-eval-baseline/report.json" in run
    # only a good run (or one accepted by hand) becomes the next baseline
    assert "success() || inputs.accept" in steps["Save it as the new baseline"]["if"]
    assert wf["permissions"] == {"contents": "read"}
    # the regular CI never talks to a model: its jobs carry no AI key
    ci = open(".github/workflows/ci.yml", encoding="utf-8").read()
    assert "AI_EVAL_API_KEY" not in ci and "METIS_API_KEY: ${{" not in ci
