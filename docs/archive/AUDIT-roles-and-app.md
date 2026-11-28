# Audit — roles & permissions (RBAC) + app health check

**Date:** 2026-07-12 · **Auditor:** live browser + API testing on the running app (company: *Default*,
logged in as Owner) + code review of the merged RBAC code (PRs #52–#61) on `main`.

## Verdict
The RBAC feature is **solid and shippable**. Enforcement is centralized, default-deny, and correctly matches
the six-role design you chose; invoices, AI, and documents all still work end-to-end after the change. Two
things to address: one **feature gap** (employee "assigned work") and one **operational note** (the app was
down after the rebuild until restarted).

---

## 1. Roles & permissions — PASS (with 1 gap)
| Check | Result |
|---|---|
| Migration made the existing login the **Owner** | ✅ `/auth/me` → `role: owner`; full access preserved |
| Central, **default-deny** guard on every business router | ✅ one `enforce_route_permission` on all routers; only `/auth` is public; unmapped route → Owner-only (fails closed) |
| Role→permission matrix matches your choices | ✅ Owner=all; CFO=books+payroll+all reports+CFO mode+approvals (no user mgmt); Accountant=books+payroll+reports (no CFO mode/approvals/settings); Manager=approvals+limited reports; Employee=own time/expenses/payslip; Viewer=read-only |
| Object-ownership (employee sees only own records) | ✅ `own_scope` applied in payroll/time/expenses; another person's payslip by id → 404 |
| Sensitive-data gating (salary, bank numbers) | ✅ enforced in the data layer, not just UI |
| **User management is company-scoped** | ✅ `User` isn't a tenant model (login needs cross-company lookup) but the handlers filter by `company_id` explicitly — verified; `/admin/users` returns only this company's users |
| Audit actor attribution | ✅ actions now record the acting user + role (was null before) |
| Low-cash / daily digest (Owner + CFO) | ✅ settings live (`enabled`, `cash_threshold`, `runway_months`, `channel`) |
| Automated test suite | ✅ full role×endpoint matrix, **live 403 tests with real per-role tokens**, "no route escapes the guard" safety net, unauthenticated→401 — merged/CI-green |

**Gap — employee "assigned work" not implemented.** You chose *"self-service **+ assigned work**"*, but
employees currently get self-service only (own time, expenses, payslip, own projects). They **cannot view
assigned clients/invoices** — those endpoints require books/reports permissions employees don't hold. The
"+ assigned clients/invoices" view is the one piece missing; everything else matches.

**Note on live enforcement:** I confirmed the Owner side live. The cross-role 403s (Employee boxed in, Viewer
read-only, salaries hidden) are proven by the automated **live-token** tests in CI — I couldn't demo them
from the browser because that needs a non-Owner login, and I don't create accounts or enter passwords. If you
create one throwaway Employee user, I'll prove it live in a minute.

## 2. Invoices — PASS
Built a 2-line invoice (one standard-VAT, one zero-rated): total computed **£3,400** (£3,000 + £400 VAT, the
zero-rated line correctly excluded from tax); the **branded PDF** rendered; the **AR posting** hit trade
debtors (confirmed via the void reversal netting 1100 to zero); **void** cleanup worked. Line-item builder,
preview, and journal all intact after RBAC.

## 3. AI accountant — PASS
- Read query → correct, grounded answer (cash balance **151,093**, matching the dashboard).
- "Record a 50 GBP stationery expense paid from the bank" → **one** proposal with the right direction
  `DR 7600 Office expenses / CR 1200 Bank`. Provider (Metis / gpt-4o-mini) healthy.

## 4. Documents & UX — PASS
- Branded invoice PDF renders. Company identity now shows in the sidebar (**"Default Books Ltd"**, replacing
  the old hardcoded "Aline Books") — the company-display fix landed.
- Password show/hide toggle present on the create-company form. Grouped sidebar + collapse/expand intact.

## 5. Multi-tenant isolation — still holding
Company scoping remains centralized; user management is explicitly scoped on top. No cross-tenant leakage
observed in the user list.

---

## Recommendations (priority order)
1. **Ship the employee "assigned work" view** — let Employees *read* the projects/clients/invoices assigned
   to them (a scoped, own-only read), to fully deliver the role you chose. Small, well-contained follow-up.
2. **Investigate the post-rebuild outage** — the app was unreachable (even `/health`) for several minutes
   after the RBAC rebuild until restarted. Confirm it wasn't a boot-time crash (check `docker compose logs
   api` after a fresh `up -d --build`); a big change + 3 migrations is exactly where a startup error can hide.
3. **Live cross-role proof** — create one throwaway Employee (and optionally Viewer) user so the 403
   enforcement and salary-gating can be demonstrated in the browser, not just in CI.

## Overall
Roles/permissions is a strong, security-first implementation (the same discipline as your tenant isolation),
and the rest of the app — invoices, AI, documents — is unaffected and healthy. Fix the one employee-access
gap and confirm the startup health, and this is production-ready.
