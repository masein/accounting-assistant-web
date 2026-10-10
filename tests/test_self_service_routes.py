"""Every route an employee or a manager can reach, reviewed for "only your own".

The mileage claim that could name anyone (#202) and the colleagues' rates and
invoice PDFs employees could read (#203) were self-service routes nobody had
checked for scope. This list is the review: each route and why it's safe for a
self-service caller. A new route that one of these roles can reach fails the
first test until it is added here — with the reason, and a test of the scope
when it holds anyone's data but the caller's.
"""
from __future__ import annotations

import uuid

from app.core.permissions import ROUTE_PERMISSIONS, role_can

SELF_SERVICE_ROLES = ("employee", "manager")

REVIEWED = {
    # shared reference data, the same for every user of the company
    ("GET", "/admin/company-profile/logo"): "the company's logo, shown in every header",
    ("GET", "/admin/display-calendar"): "the company's calendar setting",
    ("GET", "/admin/reporting-locale"): "the company's locale setting",
    ("GET", "/fx/metadata"): "currency list",
    ("GET", "/fx/rates"): "exchange rates, company or shared feed",
    ("GET", "/fx/reporting-currency"): "the base currency",
    ("POST", "/fx/convert"): "a currency conversion, no stored data",
    # approvers (managers) — company-wide by design, approvals:write
    ("GET", "/ai-accountant/approvals"): "proposals waiting for an approver; the requester can't approve their own",
    ("POST", "/ai-accountant/approvals/{token}/approve"): "the approver is the session user",
    ("POST", "/ai-accountant/approvals/{token}/reject"): "the approver is the session user",
    ("GET", "/api/mobile/v1/approvals"): "the same list as the web's, as cards; the requester's own are theirs to withdraw",
    ("POST", "/api/mobile/v1/approvals/{token}/approve"): "approve_proposal, as the web: the approver is the session user",
    ("POST", "/api/mobile/v1/approvals/{token}/reject"): "reject_proposal, as the web: the approver is the session user",
    ("GET", "/expenses/settings"): "mileage rate and threshold, needed to review claims",
    ("POST", "/expenses/{claim_id}/approve"): "the decider is the session user (#203)",
    ("POST", "/expenses/{claim_id}/reject"): "the decider is the session user (#203)",
    # expenses — own scope
    ("GET", "/expenses"): "own_scope: an employee lists only their own claims",
    ("GET", "/expenses/{claim_id}"): "own_scope: someone else's claim is 404",
    ("GET", "/expenses/pickers"): "own_scope: an employee is offered only themselves (#202)",
    ("POST", "/expenses/mileage"): "own_scope: an employee claims only for themselves (#202)",
    # notifications — per user, or role-gated by kind
    ("GET", "/notifications/feed"): "visible_to: the user's own rows and the kinds their role may see",
    ("POST", "/notifications/feed/read-all"): "visible_to",
    ("POST", "/notifications/feed/{notification_id}/read"): "visible_to: another's notification is 404",
    ("GET", "/notifications/reminders"): "the user's own reminders",
    ("POST", "/notifications/reminders"): "created for the session user",
    ("PATCH", "/notifications/reminders/{reminder_id}"): "_own_reminder",
    ("DELETE", "/notifications/reminders/{reminder_id}"): "_own_reminder",
    ("GET", "/notifications/push/key"): "the server's public VAPID key",
    ("GET", "/notifications/push/subscriptions"): "the user's own devices",
    ("POST", "/notifications/push/subscriptions"): "the device becomes the session user's",
    ("DELETE", "/notifications/push/subscriptions"): "only the user's own endpoint",
    ("POST", "/notifications/push/test"): "only the user's own devices",
    # the phone app's session: every role signs in on a phone (app/api/mobile.py)
    ("GET", "/api/mobile/v1/me"): "the session user's own profile and books",
    ("PUT", "/api/mobile/v1/me/language"): "the session user's own language",
    ("DELETE", "/api/mobile/v1/session"): "signs out the device the bearer token was issued to",
    ("GET", "/api/mobile/v1/devices"): "devices_of: the session user's own phones",
    ("DELETE", "/api/mobile/v1/devices/{device_id}"): "_own_device: another user's phone is 404",
    # payroll — own payslips
    ("GET", "/payroll/my-payslips"): "the caller's linked employee only",
    ("GET", "/payroll/runs/{run_id}/payslip/{entity_id}"): "_enforce_own_payslip: another's is 404",
    ("GET", "/payroll/runs/{run_id}/payslip/{entity_id}/pdf"): "_enforce_own_payslip",
    # petty cash — own float, managers see all
    ("GET", "/petty-cash/accounts"): "a holder lists their own account",
    ("GET", "/petty-cash/accounts/{account_id}"): "_get_account: someone else's is 403",
    ("POST", "/petty-cash/accounts/{account_id}/expenses"): "_get_account: only into your own float",
    # time — own scope
    ("GET", "/time/entries"): "own_scope: only the caller's entries",
    ("POST", "/time/entries"): "own_scope: only for yourself; payable takes the type's default (#203)",
    ("PATCH", "/time/entries/{entry_id}"): "own_scope; payable can't be set (#203)",
    ("DELETE", "/time/entries/{entry_id}"): "own_scope",
    ("GET", "/time/invoice/{invoice_id}/pdf"): "404 for a self-service caller (#203)",
    ("GET", "/time/my-summary"): "the caller's own hours",
    ("GET", "/time/pickers"): "own_scope: yourself as the worker, client names (#202)",
    ("GET", "/time/projects"): "project names and clients to log against; no budgets in the read",
    ("GET", "/time/rates"): "own_scope: your own rates (#203)",
    ("GET", "/time/unbilled"): "own_scope: your own unbilled time (#203)",
}


def _reachable() -> set[tuple[str, str]]:
    return {route for route, req in ROUTE_PERMISSIONS.items()
            if any(role_can(r, req) for r in SELF_SERVICE_ROLES)}


def test_every_self_service_route_has_been_reviewed():
    reachable = _reachable()
    new = sorted(reachable - set(REVIEWED))
    gone = sorted(set(REVIEWED) - reachable)
    assert not new, f"routes an employee or manager can now reach — review their scope and list them: {new}"
    assert not gone, f"reviewed routes no longer reachable — drop them from the list: {gone}"
    assert all(reason.strip() for reason in REVIEWED.values())


def test_a_notification_someone_else_sees_cant_be_marked_read(co, db):
    from app.db.tenant import use_company
    from app.models.notification import Notification
    session, cid = co
    with use_company(cid):
        owners = Notification(kind="api_key", title="An API key expires soon", dedupe_key=f"k-{uuid.uuid4().hex}")
        payroll = Notification(kind="payroll", title="Pay run to post", dedupe_key=f"p-{uuid.uuid4().hex}")
        db.add_all([owners, payroll])
        db.commit()
        ids = str(owners.id), str(payroll.id)
    emp = session("employee")
    for nid in ids:
        assert emp.post(f"/notifications/feed/{nid}/read").status_code == 404
    acct = session("accountant")
    assert acct.post(f"/notifications/feed/{ids[0]}/read").status_code == 404       # api_key: owners only
    assert acct.post(f"/notifications/feed/{ids[1]}/read").status_code == 200       # payroll: accountants too
    with use_company(cid):
        db.expire_all()
        assert db.get(Notification, uuid.UUID(ids[0])).read_at is None
    assert session().post(f"/notifications/feed/{ids[0]}/read").status_code == 200


from tests.test_payroll_lifecycle_http import co  # noqa: E402,F401 — fixture
