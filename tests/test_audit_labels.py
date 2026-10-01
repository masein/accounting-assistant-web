"""Every action and record type the server writes to the audit log has a label
in all four languages, so the Audit page's trail reads "ایجاد · سند" in
Persian instead of "create · transaction".

The values are read from the code itself: every audit_log / log_audit_event /
AuditLog(...) call and the two wrappers that pass an action through
(accounts._audit, transactions._log_transaction_audit). A new action or record
type without auditAct_<action> / auditEnt_<type> keys fails here."""
from __future__ import annotations

import ast
from pathlib import Path

from tests.i18n_source import JS, pack_keys

APP = Path(__file__).resolve().parents[1] / "app"
# call name → (position of the action, position of the record type — or the
# record type itself, for a wrapper that fixes it)
CALLS = {"audit_log": (None, None), "AuditLog": (None, None), "log_audit_event": (1, 2),
         "_audit": (1, "account"), "_log_transaction_audit": (1, "transaction")}


def _strings(node) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _strings(node.body) | _strings(node.orelse)
    return set()          # a variable: a wrapper passing its caller's value through


def audited_values() -> tuple[set[str], set[str]]:
    actions: set[str] = set()
    kinds: set[str] = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name not in CALLS:
                continue
            a_pos, k_pos = CALLS[name]
            kw = {k.arg: k.value for k in node.keywords}
            action = kw.get("action", node.args[a_pos] if isinstance(a_pos, int) and len(node.args) > a_pos else None)
            if "entity_type" in kw:
                kind = kw["entity_type"]
            elif isinstance(k_pos, str):
                kind = ast.Constant(k_pos)
            else:
                kind = node.args[k_pos] if isinstance(k_pos, int) and len(node.args) > k_pos else None
            actions |= _strings(action)
            kinds |= _strings(kind)
    return actions, kinds


def test_the_scan_finds_the_audit_trail():
    actions, kinds = audited_values()
    # the everyday ones, so a broken scan can't pass by finding nothing
    assert {"create", "update", "delete", "login", "lock_period", "reopen_period"} <= actions
    assert {"transaction", "account", "invoice", "user", "closed_period", "time_entry", "pay_run"} <= kinds


def test_every_audited_action_and_record_type_has_a_label_in_every_language():
    actions, kinds = audited_values()
    wanted = {f"auditAct_{a}" for a in actions} | {f"auditEnt_{k}" for k in kinds}
    missing = {lang: sorted(wanted - keys) for lang, keys in pack_keys().items() if wanted - keys}
    assert missing == {}, missing


def test_the_audit_page_uses_the_labels():
    src = (JS / "11-time-expenses-payroll.js").read_text(encoding="utf-8")
    body = src[src.index("async function loadAuditLogs"):src.index("// ═══════ CFO Module")]
    assert "auditLabel('auditAct_', l.action)" in body and "auditLabel('auditEnt_', l.entity_type)" in body
    assert "toLocaleString()" not in body          # the browser's locale, not the user's calendar
