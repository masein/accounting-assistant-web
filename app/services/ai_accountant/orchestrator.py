"""Agent orchestrator — drives the AI accountant tool-use loop.

The loop, in shape-agnostic terms (the LLM client abstracts away wire format):

1. Append the user's message to the chat history.
2. Send (system prompt + tools + ChatMessage history) via ``LLMClient.chat``
   to whichever provider is active (Anthropic or OpenAI-compatible).
3. If the response has any tool calls, execute each tool (read-tools answer
   immediately; proposal-tools persist a pending row and return a
   ``confirmation_token``), append the results as ``role: "tool"`` messages,
   and loop back to step 2.
4. When ``stop_reason == "end_turn"`` (or there are no tool calls), persist
   the assistant message to ``ai_chat_messages`` and return.

Hard safeguards:

* ``MAX_TURNS`` — caps tool-use iterations per user message. Above this we
  return the partial response and log a warning rather than rolling forever.
* Tool budget (roadmap §5.6) — at most ``tool_calls_per_message`` tool calls
  and ``proposals_per_message`` proposals per user message (platform
  ``ai_limits``). Past it the remaining calls get an error result and the
  model answers once more without tools.
* Every proposal goes through ``guardrails.review``: refused in a closed
  period, flagged for two-person approval above the company's threshold.
* ``PAUSE_TURN_RETRIES`` — handles Anthropic's `pause_turn` (server-side
  iteration cap) by re-sending; OpenAI doesn't surface this.
* All tool exceptions are caught and surfaced to the model as
  ``tool_result(is_error=True)`` so it can recover instead of crashing
  the chat turn.

Storage is in the normalized ``ChatMessage.to_dict()`` JSON shape — same
format regardless of which LLM produced or will consume the row. Sessions
can switch providers between turns without losing history (subject to the
caveat that some providers expect specific assistant↔tool↔user ordering;
both adapters in this codebase handle the canonical loop fine).
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date as _date_cls
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_accountant import AIChatMessage, AIChatSession

from .anthropic_client import AIAccountantError, AnthropicLLMClient
from .base import BaseTool, ToolContext, ToolError, ToolRegistry
from .llm_protocol import ChatMessage, LLMClient, LLMClientError, ToolCall
from .openai_client import OpenAILLMClient
from .equity_tools import register_equity_tools
from .proposal_tools import register_proposal_tools
from .read_tools import register_read_tools
from .statement_tools import register_statement_tools
from .insight_tools import register_insight_tools
from .time_tools import register_time_tools
from app.core.money import grouped, grouped_fa

logger = logging.getLogger(__name__)

# Per-message safety cap. A well-behaved chat hits the network ~3 times
# (resolve accounts → propose → wrap up). Raised to 16 to give multi-step
# tasks (resolve two account legs via search_accounts, then propose) headroom
# before the graceful-partial fallback kicks in. Still bounded for spend.
MAX_TURNS = 16
PAUSE_TURN_RETRIES = 3


SYSTEM_PROMPT = """You are the AI accountant for this company — a careful junior bookkeeper that helps the manager record transactions, look up balances, and answer accounting questions.

The current date is given at the end of these instructions ("Today:"). Treat THAT as "today" for every relative date ("today", "yesterday", "last Tuesday") and for deciding whether a date is in the future — do NOT use your own training-era notion of the current date. Any date on or before today is a valid past/present date and is fine to record.

You have a fixed catalogue of tools. You cannot write to the books directly; every write goes through a proposal that the user must explicitly confirm.

# Behavioural rules — non-negotiable

1. **Resolve names, but converge fast.** When the user names a person or organisation, call ``find_entity`` ONCE for that name. Then decide and move on — never call ``find_entity`` or ``list_entities`` repeatedly for the same name.
2. **Resolve each account leg with ``search_accounts``, then propose.** The chart uses opaque codes with no account literally named "Cash" or "Office Supplies" — do NOT guess code prefixes. Call ``search_accounts("<plain category>")`` (e.g. "office supplies", "cash") ONCE per leg, take the top match's ``code``, then call ``propose_create_transaction``. Two legs (e.g. an expense paid from cash) = two ``search_accounts`` calls, then ONE proposal. Don't keep re-querying.
   Pass the transaction's own wording as ``description`` to ``search_accounts`` and ``find_entity``: a match marked ``learned`` is what the user chose before for that wording — take it. When the user corrects a category or party and wants it kept ("always put Snapp under travel", "from now on Kim is the design supplier", "همیشه اسنپ رو بزن تو ایاب و ذهاب"), call ``propose_remember_preference`` (a confirm card) as well as fixing the entry.
3. **Never split bulk requests into a single proposal.** "Pay all overdue invoices" → list the invoices and produce one proposal per invoice. Never aggregate.
4. **Never silently round, swap currencies, or 'fix' obvious typos in amounts.** If the user says "$1k" and the vendor's invoice is in EUR, flag the mismatch and ask.
5. **Only future-dated entries are restricted.** A date on or before today always records normally — never refuse a past date. Reject only dates more than 1 day AFTER today (judged against the "Today:" line, not your own clock), and only unless the user explicitly says it's scheduled. The server enforces this too.
6. **Amounts are in the stated currency's own unit, as written — never multiply by 100.** Record the exact number the user/document gives, pence and cents included: "300 GBP" is 300 (debit 300, credit 300), NOT 30000; "£3.99" is 3.99, not 4 and not 399. Pounds, euros and dollars keep their 2 decimal places; rials have none. Do not convert to pence/cents/minor units. And never relabel the currency the user named (300 GBP stays GBP, never IRR).
7. **Refuse to help conceal, disguise, or misreport money.** This includes structuring / "smurfing" (splitting deposits or transactions into smaller amounts, or spreading them across dates, to stay under a reporting threshold), money laundering, tax evasion, fabricating or backdating documents, misclassifying personal expenses as business, and tampering with the audit trail. Judge the *intent*, not keywords: phrases like "keep it under the reporting threshold", "so the bank doesn't report it", "break it into smaller amounts to avoid…", or "spread it over a few days so it isn't flagged" are structuring even if the word never appears. When asked, briefly decline and explain why in ONE sentence, and do NOT provide the amounts, dates, splits, or a plan that would accomplish it. A single, normal transaction is always fine — only refuse when the goal is to evade reporting/detection (e.g. recording one 9,000 deposit is fine; splitting 50,000 into sub-threshold deposits to dodge reporting is not).

# Entity resolution — converge in ONE lookup (do not loop)

After a single ``find_entity`` call for a name, pick exactly one path:
* **Strong match** — top candidate confidence ≥ 0.80, or it's the only candidate: use it. Don't search again.
* **Several plausible matches** — list the top 2–3 by name and ask the user which one, then STOP and wait. Do not call find_entity/list_entities again.
* **No match, but the user is clearly naming a real party** (best < 0.50 AND they describe who it is or call them a new client/supplier/contractor/employee/bank — e.g. "Dan is a contractor", "new supplier Acme"): **propose creating the entity** instead of dropping the link. If there's also a transaction, fold it into ``propose_create_transaction`` via ``new_entities: [{name, type, role}]`` so ONE confirm card both creates the party and posts the entry linked to them — do NOT also call ``propose_create_entity`` in that turn (that produces two separate cards). Use ``propose_create_entity`` ONLY when the user is onboarding a party with no transaction ("add Acme as a client"). A freelancer / contractor / consultant / subcontractor / self-employed person you PAY for services is a **supplier** (accounts payable), NEVER an **employee** — employees are payroll staff paid wages/salary. Example — "Nina is a new freelancer, I paid her 650 for photography" → create **supplier** Nina, ``DR 7800 Professional fees 650 / CR 1200 Bank 650``. Map contractor / freelancer / subcontractor / vendor → ``supplier``, customer → ``client``. For a bank, the confirm step also creates/links its GL cash account so it's usable as a payment source. Never create an entity without the user's Confirm.
* **No usable match and the user doesn't want an entity** (they said "no supplier / nobody", or it's a vague mention you can't pin to a real party): entity links are OPTIONAL — ``propose_create_transaction`` with an EMPTY ``entity_links`` list and mention you couldn't match the name.

Never burn the whole turn budget re-listing entities. A missing entity is fine; a dead-end with no proposal is not.

# When no expense category matches

A routine spend whose word has no account of its own ("ناهار" when there is no lunch account, "taxi", "flowers") is NOT a reason to ask which account to use. Take the closest expense category ``search_accounts`` returns; if nothing reaches 0.50, use the general operating-expense account (``search_accounts("expense")`` — 6112 on the Iranian chart, 7600 on the UK chart; in personal mode the closest living-expense category, else متفرقه). Propose the card and say which category you used in one clause ("booked under general expenses").

# Gathering details when CREATING a party (one short follow-up, never an interrogation)

When the user asks to create a party WITHOUT a transaction ("add Acme as a client", "دانا رو به عنوان کارمند اضافه کن") and hasn't given contact details, ask ONCE — one short message covering everything relevant — BEFORE calling ``propose_create_entity``:
* client / supplier → ask for phone + address (and email / tax id / شماره اقتصادی if they have them handy).
* employee → ask for their BANK ACCOUNT (bank name + IBAN/شبا or account number — payroll pays them there) plus phone + address.
Fold whatever they answer into the SAME single ``propose_create_entity`` call via its phone/email/address/tax_id/economic_code/bank_name/account_number/iban/... fields — ONE confirm card, e.g. "Will create client: Acme · phone 0912… · address …". If they decline or say "just add them" ("همینو بساز", "فعلاً همین"), call the tool with what you have — the card will note what's missing; NEVER block or re-ask. If the details are already in the user's message, don't ask — just include them.

# Changing an EXISTING party — rename / fix a name / correct a type

When the user wants to CHANGE something about a party that already exists — rename it ("change the name to…", "don't keep the default name", "اسمش رو عوض کن"), or fix a wrong type (e.g. someone recorded as employee who is actually a shareholder/سهامدار) — NEVER create a new entity. Do: ``find_entity`` to locate it → ``propose_update_entity`` with its ``entity_id`` and the ``new_name`` and/or ``new_type``. One confirm card applies the change to the SAME record, keeping its transaction history. Creating a duplicate under the new name is always wrong. Shareholders / partners / founders / investors (سهامدار / شریک) are type ``shareholder``, never ``employee``.

# Completing an EXISTING / imported record — fill missing details, never re-create

When the user asks to complete or fill in details for a party that ALREADY EXISTS — "update entity <id>…", "add the IBAN for آینده جردن", the migration queue's "ask me for the missing details" hand-off (it names the entity and may give its entity id), or any wording where the party is already on file — this is an UPDATE, not a create. Ask for the missing fields (one short message, or one at a time if the user asked for that), then call ``propose_update_entity`` with the ``entity_id`` (given, or from ``find_entity``) and the answers in its detail fields (``iban`` / ``address`` / ``phone`` / ``account_number`` / …). ONE update card — "Will update bank آینده جردن · set IBAN …" — applied to the SAME record; its GL account and history stay untouched. NEVER call ``propose_create_entity`` here: a create would duplicate the party, and for a bank it would also mint a second cash account. (If you do call create with an existing name, it is auto-converted to an update of that record.)

# Resolution loop — every time the user could cause a write

a. Parse intent (record / query / invoice / etc.) and extract: amount, currency, date, entity, account, memo.
b. Resolve the entity per the rules above (one ``find_entity`` call, then commit to a path). Entities are OPTIONAL — if the user says "none"/"no supplier", skip straight to accounts.
c. Resolve each account leg with ``search_accounts("<category>")`` — one call per leg, take the top match's code. Don't fall back to guessing prefixes with ``query_ledger``.
d. Fill defaults from ``get_company_defaults`` (currency, today's date, locale) if you haven't already. Use the reporting currency unless the user stated another.
e. Draft the proposal via ``propose_create_transaction`` AS SOON AS you have the amount, date, currency and both account codes — do not keep gathering. The tool registers a pending row and returns a ``confirmation_token``; DO NOT re-paste the full summary afterwards (the chat UI renders the action card). Just briefly say what you proposed and end your turn.

Worked example — "Record a 300 GBP office-supplies expense paid from cash today" (no entity needed):
``search_accounts("office supplies")`` → 7600; ``search_accounts("cash")`` → 1200; then ``propose_create_transaction`` with currency GBP, lines [Dr 7600 300, Cr 1200 300]. Two lookups, one proposal.

# Debit/credit direction — NON-NEGOTIABLE, never let a name flip it

Money OUT (the user PAYS someone — "I paid X", "paid from the bank/cash", "spent"): **CREDIT the cash/bank account** and **DEBIT the expense** (or trade-creditors/AP if settling an existing bill). Money IN (the user RECEIVES — "received from X", "X paid us", "deposited"): **DEBIT the cash/bank account** and CREDIT revenue/AR. This is fixed by the direction of the money, NOT by who the other party is. A supplier, contractor, employee or any payee on a payment does NOT invert it — paying a supplier still CREDITS the bank.
Worked example — "I paid Dan (a supplier/contractor) 500 GBP from the bank": lines [Dr 5000 Purchases 500, Cr 1200 Bank 500] — bank CREDITED. NEVER Dr bank / Cr expense for a payment — that is a receipt, the opposite of what happened. Debit **trade creditors (2100)** ONLY when settling an EXISTING open bill/payable for that supplier; for a direct service payment to a NEW supplier with no prior bill, debit the relevant **expense** (e.g. 7800 Professional fees for a contractor/consultant, 5000 Purchases) — debiting trade creditors with no payable just leaves a debit balance on AP.

Which account on the OTHER side of cash:
* **Customer receipt** (money in from a client — "received from X", "X paid us", payment "for invoice …"): credit **trade debtors / accounts receivable** to clear the receivable — ``search_accounts("trade debtors")`` (UK 1100). If there was no prior invoice and you're recognising new income, credit **sales/revenue** (4000) instead. NEVER credit **sales returns** (a contra-revenue account, e.g. 4100) for a receipt — sales returns is ONLY for a refund/return TO a customer (money out). Worked example — "received 800 from client Acme for invoice INV-9": lines [Dr 1200 Bank 800, Cr 1100 Trade debtors 800].
* **Supplier payment**: debit the expense (or trade creditors 2100 to clear a bill), as above.

# Invoices, cheques and installments

* Questions — "which invoices are overdue?", "show invoice 1042" → ``list_invoices`` / ``get_invoice``. "Who owes us?", "whom do we owe?", "what does Acme owe us?", «حساب X چقدر مونده؟» → ``get_party_balances`` (each party's balance from the ledger; a migrated company has balances and few invoices), plus ``list_invoices`` for the open invoices. "Which cheques are due this month?", "any installment this week?", "چک‌های این ماه" → ``list_commitments``. Report balances per currency, never summed across currencies.
* A payment that names an invoice ("Acme paid invoice 1042", "paid the paper supplier's bill 77") → ``propose_record_invoice_payment`` — NOT ``propose_create_transaction``. It clears the receivable/payable itself and books any excess as credit. Only use a plain transaction when there is no invoice.
* "Invoice Acme 3 million for consulting", "record the supplier's bill" → ``propose_create_invoice`` with line items (the party must already exist — resolve it with ``find_entity`` first). Leave ``issue_date`` out (it is today) unless the user names the date: «پشتیبانی مهر» names what is billed, not when. Say the card is ready to confirm, never that the invoice "was issued".
* "The June installment was paid", "cheque 1234 cleared", "قسط این ماه رو دادم" → ``propose_settle_commitment``; "the cheque bounced / برگشت خورد" → ``propose_bounce_cheque``; "we gave a cheque for … due …" → ``propose_create_cheque`` (with the Sayad id and the invoice it pays when given); "deposited the cheque / چک رو خوابوندم", "returned it / عودت", "gave the customer's cheque to the supplier / خرج کردم" → ``propose_cheque_step``.

# Payroll, budgets and month end

* "What did we pay Sara this year?", "how much was last month's payroll?", "is September's payroll posted?" → ``get_payroll`` (with ``employee`` for one person; dates narrow it).
* "Run payroll for September", "do Shahrivar's salaries" → ``propose_run_payroll`` with the period (first and last day, in the company's calendar) — it makes a DRAFT run and posts nothing. Then "post it" → ``propose_post_pay_run``; "we paid the salaries" → ``propose_pay_pay_run``. Never record wages with ``propose_create_transaction`` when there are pay runs — the run is the record.
* "Are we over budget?", "how much of marketing's budget is left?" → ``get_budget_status``. "Budget 50 million a month for marketing" → ``propose_set_budget`` on the expense account (``months`` for several months in a row).
* "Can we close August?", "what's left for month end?" → ``get_close_checklist``; say what needs attention and that the full close pack downloads from Manager reports → Monthly close pack.
* "Close / lock the books for August" → ``get_close_checklist`` first, then ``propose_lock_period`` with the month's last day (owner only — for anyone else, say the owner does it in Settings).

# Orders, schedules, shareholders, rates, petty cash, the audit trail

* "What have we ordered from Delta?", "which POs are open?" → ``list_purchase_orders``. "What's set to go out every month?" → ``list_recurring_rules``; "pay rent of 80 million on the 1st of every month" → ``propose_create_recurring_rule`` (resolve the account with ``search_accounts`` first).
* "Who owns the company?", "what dividends do we still owe?" → ``get_cap_table``. "What dollar rate are we using?" → ``get_exchange_rates``. "How much is in petty cash?" → ``get_petty_cash``.
* "Who deleted invoice 1042?", "what did Reza change yesterday?", "what has the assistant posted this week?" → ``get_audit_trail`` (``username``, ``entity_type``, ``days``).

# Time tracking & billing clients for hours

For "log/record N hours for <person> on <client>[/<project>]", call ``propose_log_time`` — it resolves the worker, client and project by name, creates any that are new in the SAME card, resolves the billable rate, and handles relative dates. For "set <person>'s (billable) rate to X [for <client>/<project>]", call ``propose_set_billable_rate``. For "start a project called X for <client>", ``propose_create_project``. For "how many unbilled hours for <client>?", ``list_unbilled_time`` / ``get_time_summary``. For "invoice/bill <client> for [this month's / the <project>] hours", call ``propose_create_invoice_from_time`` — it aggregates UNBILLED time into a draft invoice (grouped by project then employee) and shows the exact entries/hours/value, subtotal, VAT and total before Confirm. Never invoice already-invoiced time. If a client's unbilled time spans multiple currencies the tool errors — invoice each currency separately or ask.

# Shareholder equity — contributions / capital increase / dividends / current account

These are distinct from ordinary transactions — use the dedicated equity tools so the postings hit the right equity accounts and the changes-in-equity statement reflects them. The party must already be a ``shareholder`` (سهامدار); if not, ``propose_create_entity`` type='shareholder' first (shareholders/partners/founders/سهامدار/شریک are NEVER employees). Amounts are in the currency's own unit, so "500m" → 500000000. Pick the tool by intent:
* **Contribution / آورده** — a shareholder puts money IN as capital ("Cyrus contributed 500m as capital", "سیروس ۵۰۰م آورده"): ``propose_shareholder_contribution`` (shareholder_name, amount; to_capital=false only if they explicitly say it's not yet capitalised). Posts DR bank / CR share capital.
* **Capital increase / افزایش سرمایه** ("increase registered capital by 1bn from retained earnings"): ``propose_capital_increase`` (amount, source=retained_earnings|cash|revaluation_surplus). From retained earnings it posts DR retained earnings / CR share capital.
* **Dividend / سود سهام** ("distribute/declare a 100m dividend to shareholders"): ``propose_declare_dividend`` (total_amount) — it allocates across shareholders by cap-table ownership and shows the per-shareholder split on ONE card. Posts, per holder, DR retained earnings / CR dividends payable. (Paying a declared dividend is done from the Equity screen.)
* **Shareholder current account / حساب جاری** ("Sara withdrew 50m", "شریک sara ۵۰م برداشت کرد" → direction='out'; "X lent the company 50m" → 'in'): ``propose_shareholder_current_account`` (shareholder_name, amount, direction).
Never post equity without a Confirm card. If there's no cap table yet, the dividend tool will say so — tell the user to add shareholders with ownership % first.

# Financial statements — use the deterministic tool, never hand-sum

For "balance sheet", "P&L" / "income statement", "trial balance" or "cash flow", call ``get_financial_statement`` with the right ``statement`` value and relay its totals. The figures already balance (Assets = Liabilities + Equity; trial-balance debits == credits) — do NOT reconstruct them from individual ``get_account_balance`` calls.

# Tax / VAT questions

For "how much tax/VAT do I owe", call ``get_tax_summary`` and report output, input and net tax. ALWAYS include the returned ``caveat`` verbatim and state the ``assumptions`` (the rates used) — even if the user says "just give me the number" or "skip the explanation". Never invent tax rates for other jurisdictions or filing deadlines; if asked, give the figure from the tool and say current rules/deadlines must be verified with a licensed tax professional. For cross-border / different-jurisdiction sales, don't guess a foreign VAT rate — state that the destination jurisdiction's rate must be confirmed, and note that zero-rated exports charge no output tax while reverse-charge B2B shifts the VAT to the customer (nets to zero). For "when is my filing deadline?", say you need to verify the current rules for that jurisdiction rather than asserting a date.

# Attached documents (invoice / receipt images or PDFs)

When the user's turn includes "Attached document OCR" context, treat those extracted fields (vendor, date, total, currency, line items) as the primary source for the entry. Resolve the vendor with ONE ``find_entity`` call (per the rules above), pick sensible accounts, and propose the matching transaction populated from the document — including its ``attachment_ids`` so the file links to the transaction on confirm. If the OCR text is empty or unreadable, say you couldn't read the document and ask the user to type the key details; never invent figures.

# Proactive insights — speak first when there is something to say

When the user asks how things are going, what needs attention, "چه خبر", "وضعیت چطوره", or opens with just a greeting and no task, call ``get_insights`` and relay the top items in plain language with their figures — e.g. "payroll rose 18% in Mehr because Sara joined; the internet bill is running twice its usual level". Offer ONE concrete next step per item (open the page, check the statement, chase the invoice) and ask which they'd like to look into. If the tool returns nothing, say the books look steady and ask what they'd like to do. Never invent an insight the tool didn't return.

# Bank statements — check against the books, fix step by step

When the turn carries a "Bank statement imported" context, or the user asks to check / review / reconcile / fix a statement ("صورتحساب رو چک کن", "what's different from my books"), call ``review_bank_statement`` (with the statement id if known, else the latest). Then work through the findings ONE AT A TIME, most important first, in plain language — no debit/credit jargon unless asked:
* State the finding: date, amount, narration, and what the books show.
* Propose exactly ONE fix for it and STOP — by CALLING the proposal tool in the same turn, not by asking "would you like me to record it?". The proposal card IS the question; the user answers with its Confirm/Cancel buttons, so never wait for a typed yes before calling the tool. ``unrecorded`` → ``propose_create_transaction`` with ``bank_statement_row_id`` set to the row id (this marks the statement row as posted on confirm): a bank debit / money out is Dr the row's suggested account (or ``search_accounts`` for the category) / Cr the review's ``bank_account_code``; money in is Dr ``bank_account_code`` / Cr revenue or trade debtors. Keep the row's date, the statement currency, and copy the row's ``description`` VERBATIM as the entry description — never paraphrase, shorten or translate a Persian narration (it is the bank's own text and the reconciliation matches on it). ``needs_confirmation`` → say it looks like the same entry and that they can approve the match on the Bank statements page; no posting. ``amount_mismatch`` / ``missing_in_bank`` → lay out both sides and ASK which is right; only after the user answers propose the correction (``propose_reverse_transaction`` for an entry that never happened, a new entry for the difference, …). ``balance_gap`` → always LAST: while unrecorded rows remain just say the closing balance will be re-checked once they are posted; never open with it. ``duplicate`` → mention it once, no action.
* When the review says ``bank_account_guessed`` (the statement's own bank wasn't found among the books' banks, so ``bank_account_code`` is only the chart's default), propose nothing yet: say which account it was compared with (``bank_account_name``) and ask once with ``ask_with_choices`` which of their bank accounts the statement belongs to — they choose it on the statement's card, or on the Bank statements page. A row posted to a guessed account lands on the wrong bank.
* When the user says next / continue / "بعدی", move to the next finding. If they say "post all the new ones", propose the unrecorded rows one card each. Never re-propose a row that already has a card pending, and never post a row the review says is a duplicate.

# Undoing / reversing a RECORDED entry

To undo or reverse an entry that is already recorded ("undo the last transaction", "برگردون", "معکوسش کن", "that transfer was wrong, reverse it"), call ``propose_reverse_transaction`` — it mirrors the original's legs server-side, exactly. NEVER build the reversal yourself with ``propose_create_transaction``: hand-authored legs risk repeating the original direction, which DOUBLES the error instead of undoing it. Identify the entry by id/reference when known, else ``last=true``.

# A statement posted by the user's own rules

When the turn carries «[The attached file was imported as a bank statement: statement_id=…]» (or the user names a statement already imported) and says how its rows go — which account money coming in goes to, which account money going out goes to, words like «کارمزد» to a fees account, «به تفکیک تاریخ» / "one voucher per day" — make ONE card with ``propose_post_statement``: never a card per row, never a journal built by hand. First ``search_accounts`` for every account the user names (the bank «بانک دی», «درآمد شهریه», «مخارج مدرسه», «کارمزد بانکی»…); pass the bank as ``bank_account_code`` when the user names one. A named account the chart doesn't have: ask once with ``ask_with_choices`` among the closest accounts (and offer to make a new one), never guess a code. «+» / واریز is money in, «−» / برداشت money out; fees («کارمزد») go by a rule before the out account. The bank side: when the bank the user names differs from the statement's own bank (the file is Parsian's, they said «بانک دی»), ask once with ``ask_with_choices``: the account they named, the statement's bank's own account when the books have one, or a new account for that bank when they have none. A new one is ``new_bank_name`` («بانک پارسیان») instead of ``bank_account_code``: it is made on confirm, never before, and never beside an account the books already have for that bank. Then say in a sentence what the card posts (rows, vouchers, from and to), and what it leaves out.

# Options are buttons, not a list

Whenever you ask the user to pick between options (accounts, banks, «create a new one or use this one»), call ``ask_with_choices`` with them — never type them as a bulleted or numbered list. Offer only what can be done: an account ``search_accounts`` gives as a heading (``heading_note``) takes no entries, so offer its accounts, never the heading. A code the user gives that the chart lacks («باید ۱۳۰۰ میبوده») may be the code from their own books: ``search_accounts`` with that code finds the account it became (``old_codes``); never offer to make an account just to have that number. Never judge what an account is from its code's digits: books that keep their own codes (a school's 2000 is its tuition, 1600 its creditors) number as they please, and the tools say each account's type.

# Confirmation is a BUTTON, not a message

A proposal is recorded only when the user clicks Confirm on its card. If the user types "confirm" / "تایید کن" in chat, or asks why a proposed entry isn't showing: tell them to confirm (or cancel) it on the pending card itself — do NOT call the proposal tool again; an identical second card double-posts when both get confirmed. Never re-propose something that is already pending. When the user CORRECTS a pending card ("make it 2.5 million", «مبلغش رو … کن», "no, from the other bank", "change the date") propose the corrected card with ``replaces="previous"`` (or the old card's confirmation_token): the old card is withdrawn for them. Never tell the user to cancel the old card themselves.

Never name a card's buttons or quote their labels («تأیید», «ثبت», "Confirm", "Post"): the phone and the web show them in the app's own language, which may not be the language of the chat (a Persian reply told the user to press «تأیید» on a card whose button said Post). Say the card is ready («کارت آماده است»). Don't add that nothing is recorded until they confirm: the reply stays under the card after it is confirmed, where that reads false (a posted lunch said «تا تأیید کارت، چیزی ثبت نمی‌شود», 2026-10-20).

# The app's own reviews
Each week and month the app posts a review of the books in the chat, with up to two suggestions whose buttons send a message as the user: «برای {category} بودجهٔ ماهانهٔ {amount} بگذار» / "Set a monthly budget of {amount} for {category}" → call ``propose_set_budget`` for that category and amount at once, in the currency the amount names (``currency`` OMR for «ریال عمان»; a person's budget counts that currency's spending only) (that it has no budget yet is why it was suggested; the amount is its average month); «{amount} برای هدف {goal} به {account} منتقل کن» → a transfer card from the bank or cash it comes from to that account (ask which, if there are several); "How can I catch up on my goal?" or "How can I spend less on X?" → read the figures (``get_savings_goals``, ``get_spending_summary``) and answer with two or three concrete steps from them. «دیگه مرور هفتگی نفرست», "stop the monthly review", «یادآوری ثبت نکن» → ``propose_review_prefs`` (that person only).

# Can I afford it?
"Can I afford X?", «می‌تونم … بخرم؟», "should I buy it in installments?" → ``check_affordability`` with the price (tomans ×10 to rials) and the item. The reply's card shows the figures: answer in one or two sentences with the verdict and its main number, never a list of figures. «یک هدف پس‌انداز … بساز» / "make a savings goal …" → ``propose_savings_goal`` (personal books). Never give investment advice — what to invest in, which assets or markets; say the app doesn't advise on that.

# A reply to an earlier message

A user turn that begins «[The user is replying to …: «…»]» answers that quoted message: read the user's words as being about it («و به دلار؟» under a cash answer asks the same in dollars). When the quote is a pending card (it names its confirmation_token), a correction is the corrected card with ``replaces`` set to that token — not "previous", which may be a newer card. Never say the bracketed line back to the user.

# When the user asks a pure question (read-only)

Answer with ``query_ledger`` / ``get_account_balance`` / ``list_entities``. No proposal needed. No confirmation needed.

A migrated company keeps its petty cash, loans and shared costs as accounts in its chart: «تنخواه خانم X چقدر مونده؟», «سهم هزینه مشترک با … تا حالا چقدره؟», «وام بانک ملت چقدر مونده؟» → ``search_accounts`` with those words, then ``get_account_balance`` of the account it finds (``get_petty_cash`` only knows the petty-cash module; when it finds nothing, look in the chart).

"How much cash / money do we have?" ("چقدر پول داریم؟", "موجودی نقدمون چقدره؟", "what's in the bank?") → ``get_cash_position``: it adds up EVERY cash and bank account (the cash box, each bank account, in personal mode the card and the cash on hand) and lists them. Never answer that question from one account's ``get_account_balance`` — a negative cash box next to a healthy bank account is not "an overdraft".

"Will we have enough cash?", "when do we run short?", "پیش‌بینی نقدینگی", "what if the Mellat cheque bounces?" ("اگه چک ملت برگشت بخوره چی؟"), "what if <customer> pays a month late?" → ``get_cash_forecast`` (with the scenario fields for a what-if). Quote its lowest balance and the first negative week, name the two or three items that drive it, and for a scenario give the difference against the base. The forecast is an estimate — say so once, never present it as certain.

"Who are our clients / suppliers / employees?" ("مشتری‌هامون کی‌ان", "کارمندهامون") is a MASTER-DATA question — answer it with ``list_entities`` filtered by type and list the names. Do NOT answer it from invoices or this period's transactions: a party with no recent activity (e.g. one migrated from a previous system with only an opening balance) is still a client. Mention activity only if the user asked about it.

Time words follow the company's calendar: for an Iranian (Jalali-calendar) company, "امسال / this year" means the CURRENT JALALI YEAR — from Farvardin 1 (≈ March 21) to today — never January 1. Same for "پارسال" (previous Jalali year), "این ماه" (the current Jalali month, which starts around the 21st–23rd of a Gregorian month).

"How much did I spend / earn <time word>?" ("این ماه چقدر خرج کردم؟", "how much did we spend last month?", "امسال چقدر درآمد داشتم؟") → call ``get_spending_summary`` with the matching ``period`` keyword (this_month, last_month, this_year, …). It resolves the dates in the company's calendar and returns the total, the categories and the period label — answer with its ``total`` and quote its period label. Never work out month boundaries or the date range yourself, and never conclude "nothing recorded" from a query whose dates you computed. When it returns ``by_currency``, give each currency's own total too; its ``note`` saying the total leaves a currency out means that currency's own figures are the answer, never "zero".

# Dates in replies

Copy dates. The current date is given above in both calendars — when you mention today or a period in Persian, use the Jalali string from that line or from a tool result (``today_jalali_long``, ``period.label_fa``) verbatim. Quote amounts exactly as the tool gives them, grouped («۱۹٬۶۸۱٬۸۸۲٬۴۵۳ ریال»); in Persian copy each one from its ``…_fa`` twin in the tool result (``total_fa``, ``balance_fa``), never rewrite the digits yourself. Don't restate them in میلیون / میلیارد or "billion" yourself: 19,681,882,453 is about ۱۹٫۷ میلیارد, and «۱۹٬۶۸۱ میلیارد» is a thousand times too much. Never convert a Gregorian date to Jalali (or back) yourself: 2026-09-24 is ۲ مهر ۱۴۰۵, not «۲۴ مهر». In a Jalali company every date in a tool result comes with its words beside it (``date_jalali``, ``from_date_jalali``…): quote those. For a named month or "the month before" pass ``month`` ("1405-05" is Mordad 1405) to ``get_spending_summary`` or ``get_financial_statement``, and for «امسال», «سال ۱۴۰۴» or any named year pass ``year`` ("1405") — a year before the books began has nothing: say so, never answer with another year. «گردش حساب X در <month>» is ``search_accounts`` (when X is a name) then ``query_ledger`` for that account and month; «آخرین پرداخت X کی بوده؟» is ``search_accounts`` for X then ``query_ledger`` of that account (newest first) — not the recurring rules — never work out a Jalali month's first and last day yourself (Tir has 31 days: 2026-06-22 to 2026-07-22). A tool date may be written in Jalali as YYYY-MM-DD (1405-01-01); it is converted.

# Style

* Be concise. Don't apologise. Don't lecture about accounting basics.
* LANGUAGE — non-negotiable: reply in the language of the user's LAST message. A Persian question gets a Persian answer, even if the interface language (named at the end of these instructions) is another. Only when the message has no clear language (numbers, a bare filename) fall back to the interface language. Write Persian entity names in Persian script — never transliterate them to Latin ("تولیدی آرد روشن", not "Ard roshan").
* Match the company's currency by default (returned by ``get_company_defaults``).
* TOMAN: Iranian users quote prices in تومان but the books are kept in ریال (IRR). 1 تومان = 10 ریال — ALWAYS convert: «۲۱۵ میلیون تومان» → propose 2,150,000,000 IRR, and say the conversion in your reply («۲۱۵ میلیون تومان = ۲٬۱۵۰٬۰۰۰٬۰۰۰ ریال»). Amounts said in ریال are used as-is. If the user corrects the figure, use their number.

# Refusals

If you must refuse on ethical/legal grounds, never give a bare "I can't help with that" — state the reason in ONE short sentence (e.g. "I can't help conceal income — that would be tax evasion." / "I can't fabricate receipts for expenses that didn't occur." / "I can't split that deposit to stay under the reporting threshold — that's structuring and is illegal under anti-money-laundering rules."). Do not then provide any amounts, dates, or splits that would carry out the request. For investment/financial-advice requests, give the brief caveat and suggest consulting a qualified financial advisor.
"""


# Appended to SYSTEM_PROMPT for personal-mode tenants (kind='personal',
# role='personal'). Every behavioural rule above still applies — same tools,
# same proposal flow, same refusals; this only reframes WHO you're helping
# and HOW to talk to them.
PERSONAL_MODE_ADDENDUM = """

# PERSONAL-FINANCE MODE — this tenant is a person, not a company

The books you manage here belong to ONE PERSON tracking their own daily money — not a business. Adjust accordingly:

* Say "your money / your spending / your budget", never "the company", "the books", "the manager". The user is the owner of their own finances.
* SPEAK HUMAN, NOT ACCOUNTANT. Never say debit/credit, journal entry, voucher, ledger, AR/AP in replies — say "I'll log 250,000 Toman under Food & groceries, paid from your bank account". (Proposals still need technically correct lines — the translation is in how you TALK, not in what you propose.)
* The chart of accounts is a set of everyday categories (خوراک، حمل‌ونقل، اجاره، اقساط…), listed at the end under YOUR BOOKS AT A GLANCE with the places and the people: take them from there (``search_accounts`` for anything not listed). When nothing fits, use the closest category or Miscellaneous (متفرقه) — don't interrogate the user about classification.
* Paid "نقدی / نقد / cash" → the money comes from **موجودی نقد (1120, cash on hand)**; paid by "کارت / کارت به کارت / بانک / انتقال / card / transfer" → **حساب بانکی (1110)**. ``search_accounts`` returns these first in personal mode — use its top match, don't swap them.
* Most entries have NO counterparty entity. Groceries, taxi, utility bills → empty ``entity_links``; don't ask "which supplier?". Create an entity only for a real party the user names (their landlord, a person who owes them money or whom they owe, their bank).
* SOMEONE THE USER WAS WITH IS NOT A PARTY. «ناهار با امیر», "dinner with Sara", «با بچه‌ها رفتیم سینما»: the user paid for their own meal or outing, and the person was only there. No ``new_entities``, no ``entity_links``, and don't say they were added. They are a party only when money is owed between them and the user (they paid, the user paid for them or lent to them, or someone is paying back): then use 2130 or 1150 as below. A person already in the books is linked with ``entity_links`` (``find_entity``), never made again.
* A MEAL OUT IS DINING OUT. Lunch, dinner, a restaurant, a café, takeaway («ناهار»، «شام»، «رستوران»، «کافه») goes under «تفریح و رستوران» (Leisure & dining out), even if it has never been used. «خوراک و سوپرمارکت» (Food & groceries) is food bought for home: the supermarket, fruit, bread.
* RENAMING A PLACE. When the user wants a wallet, vault or bank account called something else («اسم کیف پول سبز رو بکن کیف پول قشنگم», "call my blue wallet the travel wallet"), call ``propose_rename_place`` with the name it has now (or its code) and the new name. Its card renames the account itself, keeping its entries. Never only remember the new words for it with ``propose_remember_preference``: then the cards still show the old name.
* Typical shapes: spending = Dr expense category / Cr cash or bank · income (salary, freelance) = Dr bank / Cr income category · loan installment (قسط) = Dr installments payable / Cr bank · moving money to savings (gold, FX) = Dr the savings asset / Cr bank.
* MONEY BETWEEN THE USER AND A PERSON. Someone paid for the user («Amir paid for these», «امیر حساب کرد»): Dr the spending category / Cr «بدهی به دیگران» (2130, Money I owe). The user paid for someone or lent them money: Dr «طلب از دیگران» (1150, Money owed to me) / Cr where the money came from. Paying a person back: Dr 2130 / Cr the bank or cash it came from; being paid back: Dr the bank or cash / Cr 1150. The person is on EVERY one of these cards and named in each description («Muscat Airbnb — paid by Amir»): ``find_entity`` once; found → ``entity_links`` with their id; not in the books → the same ``new_entities`` entry on each of their cards (type ``supplier`` when the user owes them, ``client`` when they owe the user) — whichever card is confirmed first makes the person and the others are linked to them. "How much do I owe Amir?", «کی به من بدهکاره؟», "who do I owe?" → ``get_people_balances``: person by person, each currency on its own; never read the whole 2130 or 1150 account as one person's.
* A LIST in one message («$333.77 - Muscat Airbnb», one thing per line) is one card per line, each with its own amount and description, in the currency written. Stays, hotels, flights and trip tickets go under «سفر» (Travel).
* AN ANSWER NEVER DROPS THE LIST. When you asked about a list (who paid it, where the cash is) and the user answers («یکیش رو امیر بهم نقدی داده، بقیه از حساب امین پرداخت شده»: one Amir gave in cash, the rest Amin paid), make a card for EVERY item of that list in that turn: Amir's cash into the place they name (Dr the place / Cr «بدهی به دیگران», Amir on it) AND one card per remaining line, each Dr its category (Airbnb, hotel and tickets under «سفر») / Cr «بدهی به دیگران» in its own currency, with the person who paid it (Amin) on each — never only the item the question was about. Asked what they owe someone whose items they told you in this conversation but no card of theirs was confirmed («چقدر به امین بدهکارم؟»), say what they told you (the items and each currency's total) and that it isn't recorded yet, and make the cards — never answer only "nothing recorded".
* EVERYTHING IN A PLACE, or what is left in it («هرچی توی پاکت توی کشو بود دادم به همسرم»، «باقی‌مانده پاکت»، "all the money in the envelope"): that is the place's whole balance in its currency — read it from ``get_cash_position`` (``holdings``) and make the cards add up to it, so the place ends at zero. What came back goes to where they put it; what was spent and not named is one card under the category they say (ask once with ``ask_with_choices`` when they don't), for the rest. Never leave the place with money they said is gone.
* A WRONG DATE on entries already in the books («اون نه تا سفر مال شهریور بود نه این هفته»، "the Airbnb was on 15 August"): find them (``query_ledger`` gives each entry's ``transaction_id``) and call ``propose_change_date`` with each one's real date — one card for all of them. Nothing else about them changes. An entry without a date the user knows: ask once for the day or the month.
* MOVING ENTRIES TO ANOTHER CATEGORY («این سه تا خرید رو اشتباه زیر «خانواده و هدایا» ثبت کردی، ببرشون زیر «لوازم و تعمیرات خانه»», 'move these to Travel'): ONE correcting card with ``propose_create_transaction``: Dr the new category / Cr the old one, in the entries' own currency, for their total (2 + 3 + 1 = 6.000 OMR), described with what they were. No entry ids, no reversals, no wallet on it — the money already left the wallet; only its category changes. Never answer with a reminder for next time instead.
* THINGS FOR THE HOME — a power adapter, light bulbs, cleaning things, stationery, a key cut, a small repair — go under «لوازم و تعمیرات خانه» (Home supplies & repairs). «خانواده و هدایا» (Family & gifts) is only a gift, or money given to family.
* "What categories do you have?", «چه دسته‌بندی‌هایی داری؟», «دسته‌بندی‌هام چیان؟» → the categories themselves, by name, spending and income, from YOUR BOOKS AT A GLANCE — every one of them, not what was spent in them.
* MONEY THE USER ALREADY HAS IN A PLACE («۸۶ ریال توی کیف پولم دارم», «۵۰۰ دلار توی گاوصندوق دارم», «۳ میلیون تو حساب ملت هست», "I have 200 in my bank account") is a STARTING BALANCE: call ``propose_money_place`` with the place as they named it (a wallet, purse, vault, safe or box is ``cash``; a bank account or card is ``bank``), the money's OWN currency and the amount. It makes the place an account of its own the first time and records what is there against Opening net worth. Never ask where the money came from or what to set it against. A place that already has its starting balance is refused: then ask where the extra money came from.
* SEVERAL CURRENCIES. A person keeps money in several currencies, in several places — rials in a bank at home, Omani rials in a wallet, dollars in a vault — and wants each kept AS IT IS: the books never convert it. Money in another currency than the books' is recorded in that currency (``propose_money_place``, or any entry with its ``currency``); never refuse it, never convert it, never ask for a rate.
* MONEY THE USER CHANGED from one currency to another («۱۹۰۰ دلار از کیف پول والای چنج کردم شد ۷۰۲.۲ ریال عمان», "I changed 500 dollars into 192 OMR") is a card of its own: ``propose_currency_exchange`` with both amounts as said and the place (the money received stays there unless they say where it went). Never leave it out: then the dollars stay in the wallet and the rials paid from it take it below zero. When they paid with the money in the same message («…که ازش ۲۰۰ ریال دادم»), make the exchange card AND the payment's card. When it is the first money in another currency and the books' currency can still change (said at the end of these instructions), ASK ONCE first, in one short sentence, and offer the two answers with ``ask_with_choices``: count the books in that currency (then ``propose_books_currency``), or keep the books' currency and keep this money in its own (then ``propose_money_place``). Once the books have entries, don't ask: keep it in its own currency.
* CURRENCY WORDS: «تومان / تومن» is ten Iranian rials. «ریال» alone is the books' currency when that is a rial (IRR, OMR, SAR, QAR) — except money paid from or into a place that holds another rial: «۷.۱ ریال از کیف پول قشنگم», a wallet of Omani rials, is 7.100 OMR, never 7 IRR (each place's currency is in YOUR BOOKS AT A GLANCE). «ریال عمان» is OMR. «بیسه / baisa» is a thousandth of an OMANI rial: 86 rials and 100 baisa is 86.100 OMR. «درهم» is the UAE dirham (AED), «دلار» USD, «یورو» EUR, «پوند» GBP, «لیر» TRY, «دینار» needs its country. Write an amount with its currency's own places (77.500 OMR, never 77.5; «۲٫۰۰۰ ریال عمان», never «۲ ریال عمان»), copying the tool's ``…_fa`` twin when there is one.
* "How much money do I have?" in several currencies: say each currency on its own («۷۷٫۵۰۰ ریال عمان، ۵۰۰ دلار و ۳٬۰۰۰٬۰۰۰ ریال»), from ``get_cash_position``'s ``by_currency``; add them up only if the user asks, and only with rates they have.
* Name the app's places in the reply's language: the phone's account menu «ارز دفاتر» (Your books' currency); the web's «امور مالی من» (My finances). Write plain sentences: no Markdown («**»).
* Spending questions with a time word ("این ماه چقدر خرج کردم؟", "how much did I spend last month?") → ``get_spending_summary`` with the period keyword, or ``days`` for «۱۰ روز گذشته» / "the past two weeks" (add ``category_code`` when they name a category); answer in one plain sentence with the ``total`` and the period as the tool labels it. Money spent in several currencies comes back in ``by_currency``: say each currency's own total and its categories («۴۱٫۵۰۰ ریال عمان: خوراک ۱۷٫۵۰۰، خواربار ۲۰٫۲۵۰ — و ۳٬۰۰۰٬۰۰۰ ریال: خواربار»), never add them together, never call it zero or say it can't be told. «ریال عمانش رو بگو» → the same call with ``currency`` OMR. Per-category questions without a time word → ``query_ledger`` on that category.
* "How did I do last month?", "کارنامه‌ام چطوره؟" → ``get_report_card`` (a ``month`` key for another month): lead with what they saved and the rate, then the one check that needs attention. "Am I on track for the car / my emergency fund?" → ``get_savings_goals``.
* Be brief and friendly — this is a daily money diary, not a compliance interview. One confirm card per thing the user said happened.
"""


# Human-readable language names interpolated into the system prompt so the
# model knows which language to answer in (AI-2).
_LANG_NAMES = {
    "en": "English",
    "fa": "Persian (فارسی)",
    "es": "Spanish (Español)",
    "ar": "Arabic (العربية)",
}

# Localized status / fallback strings surfaced to the user (AI-2). Keyed by
# the user's preferred UI language with an English fallback.
_STATUS_STRINGS = {
    "en": {
        "max_turns_candidates": "I found these possible matches — please tell me which one (or say 'none'):\n{candidates}",
        "max_turns_dead_end": "I couldn't finish that automatically. Could you add a bit more detail (amount, date, and which account or person), and I'll propose the entry?",
        "tool_budget": "I stopped here — that was a lot of steps for one message. Ask me to carry on with the rest.",
    },
    "fa": {
        "max_turns_candidates": "این موارد احتمالی را پیدا کردم — لطفاً بگویید کدام‌یک مدنظرتان است (یا بنویسید «هیچ‌کدام»):\n{candidates}",
        "max_turns_dead_end": "نتوانستم این کار را به‌صورت خودکار کامل کنم. لطفاً کمی جزئیات بیشتر بدهید (مبلغ، تاریخ و کدام حساب یا شخص) تا سند را پیشنهاد دهم.",
        "tool_budget": "همین‌جا متوقف شدم؛ برای یک پیام مراحل زیادی شد. بگویید تا بقیه را ادامه دهم.",
    },
    "es": {
        "max_turns_candidates": "Encontré estas posibles coincidencias — dime cuál es (o escribe «ninguna»):\n{candidates}",
        "max_turns_dead_end": "No pude completarlo automáticamente. ¿Puedes dar un poco más de detalle (importe, fecha y qué cuenta o persona) y propongo el asiento?",
        "tool_budget": "Me detuve aquí: eran demasiados pasos para un solo mensaje. Pídeme que siga con el resto.",
    },
    "ar": {
        "max_turns_candidates": "وجدت هذه التطابقات المحتملة — من فضلك أخبرني أيها تقصد (أو اكتب «لا شيء»):\n{candidates}",
        "max_turns_dead_end": "لم أتمكن من إتمام ذلك تلقائياً. هل يمكنك إضافة مزيد من التفاصيل (المبلغ والتاريخ وأي حساب أو شخص) وسأقترح القيد؟",
        "tool_budget": "توقفت هنا؛ كانت خطوات كثيرة لرسالة واحدة. اطلب مني متابعة الباقي.",
    },
}


def _status(lang: str, key: str) -> str:
    pack = _STATUS_STRINGS.get(lang) or _STATUS_STRINGS["en"]
    return pack.get(key) or _STATUS_STRINGS["en"][key]


def _fold_party_into(db, txn: dict, party: dict, user_message: str | None = None, *,
                     classify: bool = True) -> None:
    """Add the party to the transaction's persisted new_entities + card so
    Confirm creates and links it. Creating a party is get-or-create by type and
    name, so on several cards the first confirmed makes it and the others link
    to it. Idempotent by name. The folded type is re-derived deterministically
    from the transaction context (a freelancer/contractor paid for services is
    a supplier, not an employee) — unless ``classify`` is off: a party already
    on another card keeps that card's type, or each card would make its own."""
    name_l = (party.get("name") or "").strip().lower()
    preview = txn.get("preview") or {}
    ptype = party.get("type")
    if classify:
        from app.services.ai_accountant.entity_create import any_staff_cost, classify_entity_type
        debit_codes = [ln.get("account_code") for ln in (preview.get("lines") or []) if ln.get("debit")]
        staff = any_staff_cost(db, debit_codes) if debit_codes else None
        text = f"{user_message or ''} {preview.get('description') or ''}"
        ptype = classify_entity_type(party.get("type"), text=text, staff_cost=staff)
    party = {**party, "type": ptype, "role": party.get("role") or ptype}
    from uuid import UUID

    from app.models.ai_accountant import AIProposal
    try:
        row = db.execute(
            select(AIProposal).where(
                AIProposal.confirmation_token == UUID(str(txn.get("confirmation_token")))
            )
        ).scalar_one_or_none()
        if row is None or row.status != "pending":
            return
        tool_input = dict(row.tool_input or {})
        ne_list = list(tool_input.get("new_entities") or [])
        if not any((e.get("name") or "").strip().lower() == name_l for e in ne_list):
            # Preserve an existing-account link for a bank party; otherwise
            # the execute path allocates a new GL bank account on Confirm.
            existing_code = party.get("account_code") if party.get("account_existing") else None
            ne_list.append({
                "name": party.get("name"), "type": ptype,
                "role": party.get("role"),
                "existing_account_code": existing_code,
                **({"shared": True} if party.get("shared") else {}),
            })
        tool_input["new_entities"] = ne_list
        row.tool_input = tool_input          # reassign so the JSON change persists
        db.commit()
    except Exception:
        db.rollback()
        return
    # Update the card payload + summary shown to the user.
    txn["preview"] = tool_input
    card_list = list(txn.get("new_entities") or [])
    if not any((e.get("name") or "").strip().lower() == name_l for e in card_list):
        card_list.append(party)
        txn["new_entities"] = card_list
        label = party.get("type")
        if party.get("type") == "bank" and party.get("account_code"):
            txn["summary"] = (txn.get("summary") or "") + (
                f"\n  Will create bank: {party.get('name')} → new cash account {party['account_code']}"
            )
        else:
            txn["summary"] = (txn.get("summary") or "") + f"\n  Will create {label}: {party.get('name')}"


# A list the user is asked to pick from, typed out: «لطفاً یکی را انتخاب کنید:» then
# «• ۶۲۱۱ «مخارج مدرسه»» and «• ۶۱۳۲ «مخارج مدرسه»», twice, with nothing to tap
# (2026-10-19, scenario AH9).
_CHOICE_BULLET = re.compile(r"^\s*(?:[•\-*–·]|[0-9۰-۹]+[.)])\s+(.+?)\s*$")
_ASKS = re.compile(r"[?؟]|\b(choose|pick|select|which|prefer)\b|انتخاب|کدام|کدوم|مشخص کنید|بگویید|"
                   r"\b(elige|elija|selecciona|cuál)\b|اختر|أي", re.IGNORECASE)


def _choices_as_chips(text: str, calls: list) -> str:
    """Options the model typed as a list after a question become the buttons
    ``ask_with_choices`` draws (2 to 4 short ones); its text keeps the question.
    A list of facts after a colon stays as it is."""
    if not text or any(c.get("name") == "ask_with_choices" for c in calls):
        return text
    lines = text.rstrip().splitlines()
    items: list[str] = []
    while lines and (m := _CHOICE_BULLET.match(lines[-1])):
        items.insert(0, m.group(1))
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    if not 2 <= len(items) <= 4 or not lines or not _ASKS.search(lines[-1]):
        return text
    options = [re.sub(r"\*\*|__|`", "", it).strip() for it in items]
    if any(not o or len(o) > 60 for o in options):
        return text
    calls.append({"tool_use_id": f"chips-{uuid.uuid4().hex[:8]}", "name": "ask_with_choices",
                  "input": {"question": lines[-1].strip(), "options": options}, "result": {"options": options}})
    return "\n".join(lines).rstrip()


def _named_in(text: str | None, name: str | None) -> bool:
    """``name`` as whole words in ``text``, case and letterforms aside."""
    from app.services.ai_accountant.entity_create import normalize_match_name
    t, n = normalize_match_name(text), normalize_match_name(name)
    return bool(n) and re.search(rf"(?<!\w){re.escape(n)}(?!\w)", t) is not None


def _link_party(db, txn: dict, entity) -> None:
    """Link an existing party to a pending transaction card: its persisted
    ``entity_links`` and the card the user sees."""
    from uuid import UUID

    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.proposal_tools import _default_role_for_type
    link = {"entity_id": str(entity.id), "role": _default_role_for_type(entity.type)}
    try:
        row = db.execute(select(AIProposal).where(
            AIProposal.confirmation_token == UUID(str(txn.get("confirmation_token"))))).scalar_one_or_none()
        if row is None or row.status != "pending":
            return
        tool_input = dict(row.tool_input or {})
        if any(str(e.get("entity_id")) == link["entity_id"] for e in tool_input.get("entity_links") or []):
            return
        tool_input["entity_links"] = [*(tool_input.get("entity_links") or []), link]
        row.tool_input = tool_input              # reassign so the JSON change persists
        db.commit()
    except Exception:  # best-effort; never break the turn
        db.rollback()
        return
    txn["preview"] = tool_input


# «ناهار با امیر», "dinner with Sara and Ali", «مع أحمد»: whoever comes after "with" was only there
_WITH = re.compile(r"(?:^|\s)(?:با|همراه|همراهِ|همراه\s+ی|with|con|مع)"
                   r"(?:\s+\S+(?:\s+\S+)?\s*(?:\s(?:و|and|y)|[,،]))*\s*$", re.IGNORECASE)


def _only_with(texts: list[str | None], name: str | None) -> bool:
    """Is ``name`` named in ``texts`` only as company — each time just after
    "with" («با», "with", "con", «مع»), or after another name joined to it —
    never as who paid, was paid or owes (scenario AK2)? False when not named."""
    from app.services.ai_accountant.entity_create import normalize_match_name
    n = normalize_match_name(name)
    if not n:
        return False
    seen = False
    for text in texts:
        t = normalize_match_name(text)
        for m in re.finditer(rf"(?<!\w){re.escape(n)}(?!\w)", t):
            seen = True
            if not _WITH.search(t[:m.start()].rstrip()):
                return False
    return seen


def _drop_party(db, txn: dict, *, name: str | None = None, entity_id: str | None = None) -> None:
    """Take a party off a pending card: a new one by ``name``, a linked one by ``entity_id``."""
    from uuid import UUID

    from app.models.ai_accountant import AIProposal
    from app.services.ai_accountant.entity_create import normalize_match_name
    key = normalize_match_name(name) if name else None

    def keep_new(e: dict) -> bool:
        return key is None or normalize_match_name(e.get("name")) != key

    def keep_link(e: dict) -> bool:
        return entity_id is None or str(e.get("entity_id")) != str(entity_id)
    try:
        row = db.execute(select(AIProposal).where(
            AIProposal.confirmation_token == UUID(str(txn.get("confirmation_token"))))).scalar_one_or_none()
        if row is None or row.status != "pending":
            return
        tool_input = dict(row.tool_input or {})
        tool_input["new_entities"] = [e for e in tool_input.get("new_entities") or [] if keep_new(e)]
        tool_input["entity_links"] = [e for e in tool_input.get("entity_links") or [] if keep_link(e)]
        row.tool_input = tool_input              # reassign so the JSON change persists
        db.commit()
    except Exception:  # best-effort; never break the turn
        db.rollback()
        return
    txn["preview"] = tool_input
    if name:
        txn["new_entities"] = [e for e in txn.get("new_entities") or [] if keep_new(e)]
        txn["summary"] = "\n".join(ln for ln in (txn.get("summary") or "").splitlines()
                                   if not (ln.strip().startswith("Will create") and ln.rstrip().endswith(f": {name}")))


def _people_on_every_card(db, proposals: list, *, personal: bool, user_message: str | None = None) -> None:
    """Whoever an entry is with is on every card of theirs this turn
    (2026-10-18, scenarios AG1–AG6). «Amir paid for these» and nine lines gave
    nine cards: only the first made and named Amir; the other eight named no
    one, and neither did «I paid Amir back 1000 dollars in cash». Model-agnostic:
    (1) a party one card makes goes on every other entry card that names no
    party yet and whose description names them (the fullest name named) —
    whichever is confirmed first makes them, the rest link to them;
    (2) in a person's books, a card of money owed between the user and someone
    (Money I owe, Money owed to me) that names one person already in the books
    is linked to them."""
    txns = [p for p in proposals if p.get("tool_name") == "propose_create_transaction"]
    if not txns:
        return
    from app.services.ai_accountant.entity_create import normalize_match_name
    made: dict[str, dict] = {}
    for t in txns:
        for ne in t.get("new_entities") or []:
            if ne.get("name") and ne.get("type") != "bank":
                made.setdefault(normalize_match_name(ne["name"]), ne)
    for t in txns:
        preview = t.get("preview") or {}
        if preview.get("new_entities") or preview.get("entity_links"):
            continue                              # the card names its party already
        named = {k for k, party in made.items() if _named_in(preview.get("description"), party["name"])}
        # the fullest name named: «paid by Amir Reza» is Amir Reza's, not Amir's too
        named = {k for k in named if not any(o != k and _named_in(o, k) for o in named)}
        for key in named:
            party = made[key]
            _fold_party_into(db, t, {**{k: party.get(k) for k in ("name", "type", "role")}, "shared": True},
                             user_message, classify=False)
            party["shared"] = True                # the card that made them says so too: made once, not on each
    if not personal:
        return
    from app.models.entity import Entity
    from app.services.ai_accountant.personal_tools import owed_between_people
    people = None
    for t in txns:
        preview = t.get("preview") or {}
        if preview.get("entity_links") or preview.get("new_entities"):
            continue
        if not any(owed_between_people(ln.get("account_code")) for ln in preview.get("lines") or []):
            continue
        if people is None:
            people = db.execute(select(Entity).where(Entity.type != "bank")).scalars().all()
        named = [e for e in people if _named_in(preview.get("description"), e.name)]
        # the fullest name named («Amir Reza», not «Amir» inside it); one name is one person
        names = {normalize_match_name(e.name) for e in named}
        names = {n for n in names if not any(o != n and _named_in(o, n) for o in names)}
        if len(names) == 1:
            _link_party(db, t, next(e for e in named if normalize_match_name(e.name) in names))
    _no_company_parties(db, txns, people, user_message)


_AMOUNT = re.compile(r"\d+(?:[.٫]\d+)?")


def _said_amount(value):
    """An amount as a comparable Decimal: 15.3, «15.300» and 15.30 are one amount."""
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError, TypeError):
        return None


def _said_amounts(text: str | None) -> set:
    """The amounts a message states, decimals kept («۱۵.۳» is 15.3, «۱٬۱۴۵» is 1145)."""
    from app.utils.jalali import _to_ascii
    plain = _to_ascii(text or "").replace("٬", "").replace(",", "")
    return {a for a in (_said_amount(m.group(0).replace("٫", ".")) for m in _AMOUNT.finditer(plain)) if a}


def _named_place_on_cards(db, proposals: list, *, personal: bool, user_message: str | None) -> None:
    """A person's money goes to the place they named (2026-10-21): the answer «کیف پول قشنگم» to «where is
    Amir's cash?» gave a card whose cash line was 112001 «کیف پول سبز». When the message names exactly one of
    the person's places — by its account's name or a name they taught the assistant for it — a card with one
    cash or bank line at another place is moved to the named one: its stored line and the card shown."""
    if not personal or not (user_message or "").strip():
        return
    txns = [p for p in proposals if p.get("tool_name") == "propose_create_transaction"]
    if not txns:
        return
    from uuid import UUID

    from app.models.account import Account
    from app.models.ai_accountant import AIProposal
    from app.models.learned_preference import LearnedPreference
    from app.services.ai_accountant.cash_tools import cash_account_codes
    from app.services.ai_accountant.money_places import _fold
    from app.services.locale_service import get_reporting_locale
    codes = set(cash_account_codes(db, locale=get_reporting_locale(db), mode="personal"))
    if not codes:
        return
    names: dict[str, str] = {}                                    # a name the person uses → the place's code
    for acc in db.execute(select(Account).where(Account.code.in_(codes))).scalars():
        names.setdefault(_fold(acc.name), acc.code)
    for pref in db.execute(select(LearnedPreference).where(LearnedPreference.account_code.in_(codes))).scalars():
        names.setdefault(_fold(pref.label or pref.pattern), pref.account_code)
    said = f" {_fold(user_message)} "
    hits = {n: c for n, c in names.items() if n and f" {n} " in said}
    # the fullest name said: «کیف پول قشنگم», not a place called «کیف» inside it
    hits = {n: c for n, c in hits.items() if not any(o != n and f" {n} " in f" {o} " for o in hits)}
    if len(set(hits.values())) != 1:
        return                                                    # none named, or two (a transfer between them)
    place = next(iter(hits.values()))
    # a message that states amounts moves only the cards of those amounts: «هرچی توی پاکت توی کشو بود … ۱۵.۳
    # ریال آورد» moved the dinner (13.7) and Lulu (33) of earlier messages onto the envelope, while the reply
    # said «کیف پول قشنگم» (owner's phone, 2026-10-25, scenario BB1). A bare answer («کیف پول قشنگم») moves any.
    stated = _said_amounts(user_message)
    for t in txns:
        lines = (t.get("preview") or {}).get("lines") or []
        at = [i for i, ln in enumerate(lines) if ln.get("account_code") in codes]
        if len(at) != 1 or lines[at[0]].get("account_code") == place:
            continue
        if stated and _said_amount(lines[at[0]].get("debit") or lines[at[0]].get("credit")) not in stated:
            continue
        try:
            row = db.execute(select(AIProposal).where(
                AIProposal.confirmation_token == UUID(str(t.get("confirmation_token"))))).scalar_one_or_none()
            if row is None or row.status != "pending":
                continue
            tool_input = dict(row.tool_input or {})
            stored = [dict(ln) for ln in tool_input.get("lines") or []]
            if len(stored) != len(lines) or stored[at[0]].get("account_code") != lines[at[0]].get("account_code"):
                continue
            stored[at[0]]["account_code"] = place
            tool_input["lines"] = stored
            row.tool_input = tool_input                           # reassign so the JSON change persists
            db.commit()
        except Exception:  # best-effort; never break the turn
            db.rollback()
            continue
        t["preview"] = tool_input


def _no_company_parties(db, txns: list, people, user_message: str | None) -> None:
    """In a person's books (2026-10-20, scenarios AK2, AK3): «ناهار با امیر», paid from
    the wallet, made Amir a "new counterparty" of a lunch he only shared — so a card
    that moves no money between the user and someone names nobody the message has
    only as company; and a person already in the books is linked, never made again
    (types are guessed per message, and a supplier Amir beside a client Amir is two)."""
    from app.models.entity import Entity
    from app.services.ai_accountant.entity_create import normalize_match_name
    from app.services.ai_accountant.personal_tools import owed_between_people
    if people is None:
        people = db.execute(select(Entity).where(Entity.type != "bank")).scalars().all()
    by_name = {normalize_match_name(e.name): e for e in people}
    for t in txns:
        preview = t.get("preview") or {}
        owed = any(owed_between_people(ln.get("account_code")) for ln in preview.get("lines") or [])
        texts = [preview.get("description"), user_message]
        for ne in list(preview.get("new_entities") or []):
            if not ne.get("name") or ne.get("type") == "bank":
                continue
            if not owed and _only_with(texts, ne["name"]):
                _drop_party(db, t, name=ne["name"])
            elif (there := by_name.get(normalize_match_name(ne["name"]))) is not None:
                _drop_party(db, t, name=ne["name"])
                _link_party(db, t, there)
        if owed:
            continue
        for link in list((t.get("preview") or {}).get("entity_links") or []):
            try:
                entity = db.get(Entity, uuid.UUID(str(link.get("entity_id"))))
            except (ValueError, TypeError):
                continue
            if entity is not None and entity.type != "bank" and _only_with(texts, entity.name):
                _drop_party(db, t, entity_id=str(entity.id))


def _collapse_redundant_entity_proposals(db, proposals: list, turn_start: int,
                                         user_message: str | None = None) -> None:
    """One-card UX, model-agnostic. If a single turn emits BOTH a standalone
    ``propose_create_entity`` for party X AND a ``propose_create_transaction``
    that references X — whether X is in the transaction's ``new_entities`` or
    just named in its description — fold X into the transaction's ``new_entities``
    (so Confirm creates + links X) and drop the standalone create. The party can
    only ever be referenced by name here: ``entity_links`` require an existing
    UUID, so a brand-new party never appears there.

    Works across the WHOLE chat turn (all LLM turns), since the model often
    emits the standalone create and the transaction/time action in SEPARATE
    turns. The standalone path survives only when nothing else this turn uses
    the party ("add Acme as a client")."""
    turn = proposals[turn_start:]
    if len(turn) < 2:
        return

    # Proposals that already CREATE/link the party themselves (so a separate
    # propose_create_entity for the same party is redundant). A transaction also
    # gets the party folded into its new_entities; the time tools bundle creation.
    _ABSORB = {
        "propose_create_transaction", "propose_log_time",
        "propose_create_invoice_from_time",
    }
    absorbers = [p for p in turn if p.get("tool_name") in _ABSORB]
    txns = [p for p in turn if p.get("tool_name") == "propose_create_transaction"]
    entities = [p for p in turn if p.get("tool_name") == "propose_create_entity"]
    if not absorbers or not entities:
        return

    from uuid import UUID

    from app.models.ai_accountant import AIProposal

    def _party(ent: dict) -> dict | None:
        nes = ent.get("new_entities") or []
        return nes[0] if nes else None

    def _references(absorber: dict, name: str) -> bool:
        """Does this absorber create/link/name the party? Checks its
        new_entities, named preview fields (worker/client/project), and a
        substring of its description."""
        name_l = name.strip().lower()
        if not name_l:
            return False
        preview = absorber.get("preview") or {}
        for ne in (preview.get("new_entities") or []) + (absorber.get("new_entities") or []):
            if (ne.get("name") or "").strip().lower() == name_l:
                return True
        for k in ("employee_name", "client_name", "project_name", "name"):
            if str(preview.get(k) or "").strip().lower() == name_l:
                return True
        if str(absorber.get("new_project") or "").strip().lower() == name_l:
            return True
        return name_l in (preview.get("description") or "").lower()

    def _cancel(token: str) -> None:
        try:
            row = db.execute(
                select(AIProposal).where(AIProposal.confirmation_token == UUID(str(token)))
            ).scalar_one_or_none()
            if row is not None and row.status == "pending":
                row.status = "cancelled"
                db.commit()
        except Exception:  # best-effort; never break the turn
            db.rollback()

    dropped: set[int] = set()
    for ent in entities:
        party = _party(ent)
        if not party or not party.get("name"):
            continue
        if not any(_references(a, party["name"]) for a in absorbers):
            continue  # nothing else this turn uses the party — keep standalone
        # If a transaction references it, fold the party in so Confirm creates +
        # LINKS it (time tools already bundle creation, so no fold needed there).
        txn_match = next((t for t in txns if _references(t, party["name"])), None)
        if txn_match is not None:
            _fold_party_into(db, txn_match, party, user_message)
        _cancel(ent.get("confirmation_token"))
        dropped.add(id(ent))

    if dropped:
        proposals[turn_start:] = [p for p in turn if id(p) not in dropped]


# Persian/English magnitude words: «۲۱۵ میلیون» = 215,000,000. Written with
# both Persian (ی) and Arabic (ي) letterforms since users type either.
_MAGNITUDE_WORDS = {
    "هزار": 1_000, "thousand": 1_000,
    "میلیون": 1_000_000, "ميليون": 1_000_000, "ملیون": 1_000_000, "million": 1_000_000,
    "میلیارد": 1_000_000_000, "ميليارد": 1_000_000_000, "billion": 1_000_000_000,
}
_MAG_ALTS = "|".join(_MAGNITUDE_WORDS)
# one "<number> <magnitude>" term
_MAG_TERM_RE = re.compile(rf"(\d[\d,٬\.]*)\s*({_MAG_ALTS})")
# a compound of such terms joined by و/and: «۲ میلیارد و ۱۵۰ میلیون»
_MAG_COMPOUND_RE = re.compile(
    rf"(\d[\d,٬\.]*)\s*(?:{_MAG_ALTS})(?:\s*(?:و|and)\s*(\d[\d,٬\.]*)\s*(?:{_MAG_ALTS}))+"
)
_TOMAN_RE = re.compile(r"تومان|تومن|تومــان|toman", re.IGNORECASE)


_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def _numbers_in_text(text: str | None) -> list[int]:
    """Extract candidate monetary amounts from free text (Persian-digit
    aware), for the proposal amount-sanity cross-check. Handles plain and
    grouped numbers, the 'k'/'m' shorthand, Persian magnitude words
    («۲۱۵ میلیون», compounds like «۲ میلیارد و ۱۵۰ میلیون»), and — when the
    text prices in تومان — the ×10 rial equivalent of every amount, so a
    correct toman→rial conversion is never rejected as a mismatch."""
    if not text:
        return []
    from app.services.ocr_extract import coerce_amount, normalize_digits

    # Identifiers aren't amounts: the statement-review button sends the
    # statement's UUID, whose digit runs used to become "source amounts" and
    # refuse every card the review proposed (roadmap §5.5 eval).
    text = _UUID_RE.sub(" ", text)
    norm = normalize_digits(text)
    out: list[int] = []
    # number optionally followed by a k/m magnitude suffix
    for m in re.finditer(r"(\d[\d,٬\.]*)\s*([kKmM])?", norm):
        digits = m.group(1)
        suffix = (m.group(2) or "").lower()
        base = coerce_amount(digits)
        if base is None:
            continue
        if suffix == "k":
            base *= 1_000
        elif suffix == "m":
            base *= 1_000_000
        if base >= 1:
            out.append(base)
    # magnitude-word terms: «۲۱۵ میلیون» → 215,000,000
    for m in _MAG_TERM_RE.finditer(norm):
        base = coerce_amount(m.group(1))
        if base:
            out.append(base * _MAGNITUDE_WORDS[m.group(2)])
    # compounds: «۲ میلیارد و ۱۵۰ میلیون» → 2,150,000,000
    for m in _MAG_COMPOUND_RE.finditer(norm):
        total = 0
        for term in _MAG_TERM_RE.finditer(m.group(0)):
            base = coerce_amount(term.group(1))
            if base:
                total += base * _MAGNITUDE_WORDS[term.group(2)]
        if total:
            out.append(total)
    # toman pricing: the books are in rial — accept the ×10 conversion too
    if _TOMAN_RE.search(norm):
        out.extend([a * 10 for a in out])
    return out


# ---------------------------------------------------------------------------
# Registry building
# ---------------------------------------------------------------------------


def build_default_registry() -> ToolRegistry:
    """Return a fully-populated tool registry: read tools + proposal tools."""
    reg = ToolRegistry()
    register_read_tools(reg)
    register_statement_tools(reg)
    # a short question's answers to tap, in a company's books too: «مخارج مدرسه» isn't in the chart,
    # which of these? came as a list to type back (real model, 2026-10-18)
    from app.services.ai_accountant.money_places import AskWithChoices
    reg.register(AskWithChoices())
    register_insight_tools(reg)
    register_proposal_tools(reg)
    register_time_tools(reg)
    register_equity_tools(reg)
    from app.services.ai_accountant.asset_tools import register_asset_tools
    from app.services.ai_accountant.commitment_tools import register_commitment_tools
    from app.services.ai_accountant.inventory_tools import register_inventory_tools
    from app.services.ai_accountant.invoice_tools import register_invoice_tools
    register_invoice_tools(reg)
    register_commitment_tools(reg)
    register_asset_tools(reg)
    register_inventory_tools(reg)
    from app.services.ai_accountant.memory_tools import register_memory_tools
    register_memory_tools(reg)
    from app.services.ai_accountant.date_tools import register_date_tools
    register_date_tools(reg)                       # an entry's real date, from the chat (BB4)
    from app.services.ai_accountant.review_tools import register_review_tools
    register_review_tools(reg)                     # «stop the weekly review» (AL14)
    from app.services.ai_accountant.afford_tools import register_afford_tools
    register_afford_tools(reg, personal=False)     # can we afford it? (AM1–AM5)
    from app.services.ai_accountant.payroll_tools import register_payroll_tools
    from app.services.ai_accountant.period_tools import register_period_tools
    register_payroll_tools(reg)
    register_period_tools(reg)
    from app.services.ai_accountant.ops_tools import register_ops_tools
    register_ops_tools(reg)
    return reg


def build_personal_registry() -> ToolRegistry:
    """Personal-finance mode: reads + core proposals only. No time-billing,
    no equity — those tools talk SME concepts (projects, billable rates,
    dividends) that don't exist for a personal tenant, and dropping them
    keeps the model from ever proposing one."""
    reg = ToolRegistry()
    register_read_tools(reg)
    register_statement_tools(reg)
    register_insight_tools(reg)
    register_proposal_tools(reg)
    from app.services.ai_accountant.commitment_tools import register_commitment_tools
    register_commitment_tools(reg, personal=True)  # loans / installments exist for a person too
    from app.services.ai_accountant.memory_tools import register_memory_tools
    register_memory_tools(reg)                     # "always put Snapp under transport" works for a person too
    from app.services.ai_accountant.personal_tools import register_personal_tools
    register_personal_tools(reg)                   # report card + savings goals (§4.12)
    from app.services.ai_accountant.money_places import register_money_tools
    register_money_tools(reg)                      # money where it's kept, in its own currency (scenarios AB1–AB6)
    from app.services.ai_accountant.date_tools import register_date_tools
    register_date_tools(reg)                       # entries told without their dates, dated right (BB4)
    from app.services.ai_accountant.period_tools import GetBudgetStatus, ProposeSetBudget
    reg.register(GetBudgetStatus())                # a review suggests a budget: «… بودجهٔ ماهانه بگذار» (AL9)
    reg.register(ProposeSetBudget())
    from app.services.ai_accountant.review_tools import register_review_tools
    register_review_tools(reg)                     # «دیگه مرور هفتگی نفرست» (AL14)
    from app.services.ai_accountant.afford_tools import register_afford_tools
    register_afford_tools(reg, personal=True)      # can I afford it? and its savings goal (AM1–AM10)
    return reg


# ---------------------------------------------------------------------------
# LLM client selection
# ---------------------------------------------------------------------------


def _resolve_chat_shape(db: Session) -> str:
    """Read the active chat-provider shape. Defaults to 'anthropic' when an
    Anthropic key is configured (legacy behaviour), 'openai' otherwise.

    Persisted platform-wide in ``app_settings`` under ``ai_chat_provider_shape``
    once the super-admin picks one in Settings — until then this auto-detect
    rule applies (``ai_runtime.effective_chat_shape``).
    """
    from app.core.ai_runtime import effective_chat_shape
    return effective_chat_shape(db)


def _get_chat_client(shape: str) -> LLMClient:
    if shape == "openai":
        return OpenAILLMClient()
    return AnthropicLLMClient()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _get_or_create_session(
    db: Session, *, user_id: str, session_id: str | None
) -> AIChatSession:
    """Look up the chat session or create a fresh one."""
    if session_id:
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            sid = None
        if sid:
            row = db.execute(
                select(AIChatSession).where(AIChatSession.id == sid)
            ).scalar_one_or_none()
            if row is not None:
                if row.user_id != user_id:
                    raise PermissionError("This chat session belongs to a different user.")
                return row
    row = AIChatSession(user_id=user_id, title=None)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def sent_files(db: Session, attachment_ids: list[str]) -> list[dict[str, Any]]:
    """The files a message carried, as a conversation shows them again: name and type."""
    from app.models.transaction import TransactionAttachment
    out = []
    for raw in attachment_ids:
        try:
            att = db.get(TransactionAttachment, uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            att = None
        if att is not None:
            out.append({"name": att.file_name, "content_type": att.content_type})
    return out


def _persist_message(db: Session, *, session_id: uuid.UUID, role: str, content: dict[str, Any]) -> None:
    db.add(AIChatMessage(session_id=session_id, role=role, content=content))
    db.commit()


def maybe_autotitle_session(db: Session, session: AIChatSession, user_message: str) -> None:
    """Name an untitled session from its first user message (~6 words), the
    ChatGPT convention — the user can rename it in the sidebar any time."""
    if session.title:
        return
    words = (user_message or "").strip().split()
    title = " ".join(words[:6])[:60]
    if title:
        session.title = title
        db.commit()


_JALALI_ISO = re.compile(r"^\s*(1[34]\d\d)[-/](\d{1,2})[-/](\d{1,2})\s*$")


_DATE_KEY = re.compile(r"date|^(on|as_of|day|due|start|end)$", re.IGNORECASE)


_CODE_KEY = re.compile(r"code|query|reference|number|invoice|cheque", re.IGNORECASE)


def _ascii_digits(value, key: str = ""):
    """Persian and Arabic digits in codes and searches as ASCII: «۱۱۱۰۰۵۰۰۱»
    found no account (AI QA on migrated books, 2026-10-12)."""
    if isinstance(value, dict):
        return {k: _ascii_digits(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_ascii_digits(v, key) for v in value]
    if isinstance(value, str) and _CODE_KEY.search(key):
        from app.utils.jalali import _to_ascii
        return _to_ascii(value).translate(_ARABIC_DIGITS)
    return value


_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _gregorian_dates(value, key: str = ""):
    """A tool's input with Jalali dates written as ISO ("1405-01-01") turned
    Gregorian, in the fields that hold dates. The model sent this year's
    Jalali bounds and the statement read them as the year 1405 AD: «سود و زیان
    امسال» came back zero (AI QA on migrated books, 2026-10-12); a card would
    have been dated 1405 AD."""
    if isinstance(value, dict):
        return {k: _gregorian_dates(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_gregorian_dates(v, key) for v in value]
    if isinstance(value, str) and _DATE_KEY.search(key):
        from app.utils.jalali import _to_ascii, jalali_to_gregorian
        m = _JALALI_ISO.match(_to_ascii(value))
        if m:
            try:
                return jalali_to_gregorian(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                return value
    return value


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


_MONEY_KEY = re.compile(r"amount|total|balance|debit|credit|revenue|income|expense|profit|cash|burn|net|gross|"
                        r"value|paid|due|owed|tax|price|cost|limit|actual|spent|left", re.IGNORECASE)


def _with_places(v, ccy: str) -> str:
    """«۲٫۰۰۰», «۷٫۴۵۰»: an amount in Persian to its currency's places."""
    from app.core.money import exponent, persian_figures, to_decimal
    places = exponent(ccy)
    return persian_figures(f"{to_decimal(v):,.{places}f}")


def _for_the_model(value, key: str = "", *, jalali: bool = False, persian: bool = False, ccy: str | None = None):
    """A tool result as the model reads it. In a Jalali company every ISO day
    in a date field gets its Jalali words beside it («date_jalali»: «۶ مرداد
    ۱۴۰۵»): left to convert 2026-07-28 itself, the model wrote «۷ مرداد». For a
    Persian reader every amount gets its Persian digits (``total_fa``:
    «۲۱٬۷۲۱٬۲۰۰٬۲۸۳»): rewriting 21,721,200,283 digit by digit it wrote
    «۲۱٬۷۱۲٬۱۰۰٬۲۸۳» (AI QA on migrated books, 2026-10-12). The prompt has it
    copy these."""
    if isinstance(value, dict):
        out = {}
        # the currency these amounts are in, from this level or the one above: an Omani rial's baisa are
        # always written («۲٫۰۰۰ ریال عمان», never «۲ ریال عمان», 2026-10-21)
        own = value.get("currency")
        ccy = own.strip().upper() if isinstance(own, str) and 3 <= len(own.strip()) <= 8 else ccy
        from app.core.money import exponent
        places = exponent(ccy) if ccy else 0
        for k, v in value.items():
            k = str(k)
            out[k] = _for_the_model(v, k, jalali=jalali, persian=persian, ccy=ccy)
            if jalali and isinstance(v, str) and _DATE_KEY.search(k) and _ISO_DAY.match(v):
                from app.utils.jalali import format_jalali_long
                try:
                    out[f"{k}_jalali"] = format_jalali_long(_date_cls.fromisoformat(v))
                except ValueError:
                    pass
            if persian and _MONEY_KEY.search(k) and not isinstance(v, bool) and isinstance(v, (int, float, Decimal)):
                if places:
                    out[f"{k}_fa"] = _with_places(v, ccy)        # every amount, to its places
                elif abs(v) >= 1000:
                    out[f"{k}_fa"] = grouped_fa(v)
        return out
    if isinstance(value, list):
        return [_for_the_model(v, key, jalali=jalali, persian=persian, ccy=ccy) for v in value]
    return value


def _with_jalali_labels(value, key: str = ""):
    return _for_the_model(value, key, jalali=True)


_ARABIC_SCRIPT = re.compile(r"[\u0600-\u06FF]")
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")


def _message_language(message: str | None, lang: str) -> str:
    """The language to answer in: the reader's, unless the message is plainly
    in the other script — "What was our biggest expense last month?" from a
    Persian reader was answered in Persian (AI QA, 2026-10-12)."""
    text = message or ""
    latin = len(_LATIN_WORD.findall(text))
    if lang in ("fa", "ar") and latin >= 3 and not _ARABIC_SCRIPT.search(text):
        return "en"
    if lang in ("en", "es") and _ARABIC_SCRIPT.search(text) and latin == 0:
        return "fa"
    return lang


def thread_language(db, session_id, lang: str) -> str:
    """The language the person writes in this thread: their last message's, else the interface's. A phone
    in English whose owner writes Persian got the briefing in English mid-conversation (owner's phone,
    2026-10-22, scenario AZ5)."""
    if not session_id:
        return lang
    from app.models.ai_accountant import AIChatMessage
    try:
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    except (ValueError, TypeError):
        return lang
    last = db.execute(select(AIChatMessage).where(AIChatMessage.session_id == sid, AIChatMessage.role == "user")
                      .order_by(AIChatMessage.created_at.desc()).limit(1)).scalars().first()
    said = ((last.content or {}).get("text") if last is not None else None) or ""
    return _message_language(said, lang) if said.strip() else lang


# Refusals that name their own fix: a reply that ends the turn without the fixed
# card gets the refusal once more. A closed period or a future date is final.
_CORRECTABLE_REFUSALS = {"toman_not_converted", "unused_seed_account", "place_currency", "stated_currency"}


_FIGURE = re.compile(r"[0-9۰-۹]{1,3}(?:[,٬،][0-9۰-۹]{3}){2,}")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _tool_figures(value, out: set[int]) -> set[int]:
    if isinstance(value, dict):
        for v in value.values():
            _tool_figures(v, out)
    elif isinstance(value, list):
        for v in value:
            _tool_figures(v, out)
    elif isinstance(value, (int, Decimal)) and not isinstance(value, bool) and abs(value) >= 1_000_000:
        if value == int(value):
            out.add(abs(int(value)))
    return out


_STRAY_TAIL = re.compile(r"([.!؟?])\s*[A-Za-z]{3,24}\s*$")


def _no_stray_tail(text: str) -> str:
    """A Persian or Arabic reply that ends in a lone Latin word glued after its
    last full stop: «…امکان ثبت مطمئن این افتتاحیه را ندارم.cuntegn» (the
    owner's phone, 2026-10-15). The model's own stray token, not a word of the
    answer: it goes. A reply in Latin script, or one ending in a word that has
    a sentence of its own, is left alone."""
    m = _STRAY_TAIL.search(text or "")
    if not m or not _ARABIC_SCRIPT.search(text[:m.start()]):
        return text
    return text[:m.start() + 1]


def _true_figures(text: str, tool_call_log: list[dict]) -> str:
    """The reply's long figures as the tools gave them. Copying 21,721,200,283
    the model wrote «۲۱٬۷۱۲٬۱۰۰٬۲۸۳», and 1,017,516,253 as «۱٬۰۱۷٬۵۱۷٬۲۵۳» (AI
    QA on migrated books, 2026-10-12). A grouped figure no tool returned, a few
    digits off (or reordered) from exactly one figure a tool did, is that
    figure, written as the reply wrote it (three digits for one of ten digits
    or more). Anything else is left alone: a sum the model worked out is not
    near a single tool figure. Nor is a round one, a sum or difference of two
    the tools gave, or one whose first digit is another: «بدهی‌تان ۳٬۰۰۰٬۰۰۰
    ریال باقی می‌ماند», 5,000,000 owed less 2,000,000 paid, went out as
    «۲٬۰۰۰٬۰۰۰», the card's own amount (2026-10-20)."""
    figures: set[int] = set()
    for entry in tool_call_log or []:
        _tool_figures(entry.get("result"), figures)
    if not figures or not text:
        return text

    def fix(m):
        token = m.group(0)
        digits = re.sub(r"[,٬،]", "", token).translate(_FA_DIGITS)
        n = int(digits)
        if n in figures or len(digits.rstrip("0")) <= 4:    # 3,000,000 is worked out, never a slip copying a figure
            return token
        if any(n in (a + b, abs(a - b)) for a in figures for b in figures if a != b):
            return token                                    # what's left, or a total, of the tools' own figures
        slips = 3 if len(digits) >= 10 else 2           # «۲۱٬۷۱۲٬۱۰۰٬۲۸۳» is three digits off 21,721,200,283
        near = [f for f in figures if len(str(f)) == len(digits) and str(f)[0] == digits[0]
                and (sorted(str(f)) == sorted(digits) or sum(a != b for a, b in zip(str(f), digits)) <= slips)]
        if len(near) != 1:
            return token
        sep = "٬" if "٬" in token else ("،" if "،" in token else ",")
        right = f"{grouped(near[0])}".replace(",", sep)
        if any("۰" <= ch <= "۹" for ch in token):
            from app.utils.jalali import to_persian_digits
            right = to_persian_digits(right)
        logger.warning("ai-accountant: corrected a figure the model miscopied: %s → %s", token, right)
        return right

    return _FIGURE.sub(fix, text)


def _withdraw_replaced(db: Session, replaces: str | None, *, user_id: str, session_id: str | None,
                       tool_name: str, new_token: str) -> str | None:
    """Withdraw the card a corrected one replaces: this user's, in this
    conversation, still pending. ``"previous"`` is the latest of the same kind.
    The token it withdrew, or None (nothing pending matched: a made-up token
    is ignored, never someone else's card)."""
    if not replaces or not session_id:
        return None
    from app.models.ai_accountant import AIProposal
    q = (select(AIProposal)
         .where(AIProposal.user_id == user_id, AIProposal.session_id == session_id, AIProposal.status == "pending",
                AIProposal.confirmation_token != uuid.UUID(str(new_token))))
    if replaces.strip().lower() == "previous":
        old = db.execute(q.where(AIProposal.tool_name == tool_name)
                         .order_by(AIProposal.created_at.desc())).scalars().first()
    else:
        try:
            token = uuid.UUID(replaces.strip())
        except ValueError:
            return None
        old = db.execute(q.where(AIProposal.confirmation_token == token)).scalars().first()
    if old is None:
        return None
    old.status = "cancelled"
    if old.approval_status == "requested":
        old.approval_status = "withdrawn"
    from app.core.money import json_dumps
    from app.services.audit_service import log_audit_event
    log_audit_event(db, action="update", entity_type="ai_proposal", entity_id=str(old.id),
                    detail=json_dumps({"tool": old.tool_name, "replaced_by": str(new_token), "via": "chat"}),
                    user_id=str(user_id))
    db.flush()
    return str(old.confirmation_token)


def _replay_history(db: Session, session_id: uuid.UUID) -> list[ChatMessage]:
    """Rebuild the normalized ``ChatMessage`` history from saved rows."""
    rows = (
        db.execute(
            select(AIChatMessage)
            .where(AIChatMessage.session_id == session_id)
            .order_by(AIChatMessage.created_at, AIChatMessage.id)
        )
        .scalars()
        .all()
    )
    history: list[ChatMessage] = []
    for row in rows:
        content = row.content or {}
        # Old (pre-protocol) format used to stash Anthropic-shape blocks
        # under a "content" key; if we encounter one of those, do a
        # best-effort conversion so old sessions don't break entirely.
        if "role" in content:
            history.append(ChatMessage.from_dict(content))
        elif row.role == "user" and "content" in content and isinstance(content["content"], str):
            # Legacy: {"content": "user text"}
            history.append(ChatMessage(role="user", text=content["content"]))
        # Older assistant + tool entries from before this refactor are
        # dropped on replay — they're in Anthropic-block shape and would
        # confuse an OpenAI adapter. The user can /reset the session.
    return _compact_history(_cards_as_they_stand(db, history))


# How much of a thread goes back to the model with each message (2026-10-22). The whole thread went, every
# tool result's JSON included, on each of a message's two or three calls: a long thread cost 50–80k tokens a
# message, and the owner reached 2.1M in a day. The last exchanges go as they were; older ones as what was said
# and one line per card; the oldest not at all — the books keep what was recorded, and the tools read it.
KEEP_WHOLE = 2           # exchanges (a user message and everything after it) replayed as they were
KEEP_COMPACT = 30        # older exchanges replayed compact; those before them are left out
_LEFT_OUT = ("[Earlier messages of this thread are left out. What was recorded is in the books — read it with "
             "the tools, never from memory.]")


def _card_line(msg: ChatMessage) -> str | None:
    """A card's tool result in one line: what it is, what became of it, and its token (a card can be
    replaced by its token)."""
    import json as _json
    if msg.role != "tool" or msg.is_error or not msg.text or '"confirmation_token"' not in msg.text:
        return None
    try:
        body = _json.loads(msg.text)
    except ValueError:
        return None
    if not isinstance(body, dict) or not body.get("confirmation_token"):
        return None
    summary = " ".join(str(body.get("summary") or body.get("tool") or "card").split())[:200]
    return f"{summary} — {body.get('status') or 'pending'} (card {body['confirmation_token']})"


def _compact_exchange(exchange: list[ChatMessage]) -> list[ChatMessage]:
    """What was said and what came of it: the user's words, the assistant's last answer, and a line per card;
    no tool calls or tool results."""
    out: list[ChatMessage] = []
    rest = exchange
    if exchange and exchange[0].role == "user":
        out.append(ChatMessage(role="user", text=exchange[0].text or ""))
        rest = exchange[1:]
    said = [m.text.strip() for m in rest if m.role == "assistant" and (m.text or "").strip()]
    cards = [line for line in (_card_line(m) for m in rest) if line]
    text = "\n".join(x for x in (said[-1] if said else "",
                                  ("[Cards: " + "; ".join(cards) + "]") if cards else "") if x)
    if text:
        out.append(ChatMessage(role="assistant", text=text))
    return out


def _compact_history(history: list[ChatMessage]) -> list[ChatMessage]:
    exchanges: list[list[ChatMessage]] = []
    for msg in history:
        if msg.role == "user" or not exchanges:
            exchanges.append([msg])
        else:
            exchanges[-1].append(msg)
    if len(exchanges) <= KEEP_WHOLE:
        return history
    older, recent = exchanges[:-KEEP_WHOLE], exchanges[-KEEP_WHOLE:]
    left_out = len(older) > KEEP_COMPACT
    older = older[-KEEP_COMPACT:]
    out: list[ChatMessage] = []
    for ex in older:
        out.extend(_compact_exchange(ex))
    if left_out and out:
        first = out[0]
        out[0] = ChatMessage(role=first.role, text=f"{_LEFT_OUT}\n\n{first.text or ''}")
    for ex in recent:
        out.extend(ex)
    return out


_CARD_STATES = {
    "executed": "The user confirmed it: it is in the books now. Speak of it as recorded.",
    "cancelled": "The user cancelled it, or it was replaced: nothing of it was recorded.",
    "expired": "It expired before the user confirmed it: nothing of it was recorded.",
}


def _cards_as_they_stand(db: Session, history: list[ChatMessage]) -> list[ChatMessage]:
    """A card's tool result was saved saying «pending», and replayed so for
    ever: right after the user confirmed nine cards the model said «once you
    confirm the nine pending cards» (2026-10-18, scenario AG8). Each card's
    result is replayed with what became of it: confirmed, cancelled or expired."""
    import json as _json

    pending: dict[int, dict] = {}
    for i, msg in enumerate(history):
        if msg.role != "tool" or msg.is_error or not msg.text or '"confirmation_token"' not in msg.text:
            continue
        try:
            body = _json.loads(msg.text)
        except ValueError:
            continue
        if isinstance(body, dict) and body.get("status") == "pending" and body.get("confirmation_token"):
            pending[i] = body
    if not pending:
        return history
    tokens = []
    for body in pending.values():
        try:
            tokens.append(uuid.UUID(str(body["confirmation_token"])))
        except ValueError:
            pass
    if not tokens:
        return history
    from datetime import datetime, timezone

    from app.models.ai_accountant import AIProposal
    from .proposal_tools import PROPOSAL_TTL
    rows = {str(tok): (status, made, approval) for tok, status, made, approval in db.execute(
        select(AIProposal.confirmation_token, AIProposal.status, AIProposal.created_at, AIProposal.approval_status)
        .where(AIProposal.confirmation_token.in_(tokens))).all()}
    now = datetime.now(timezone.utc)
    for i, body in pending.items():
        hit = rows.get(str(body["confirmation_token"]))
        if hit is None:
            continue
        state, made, approval = hit
        if made is not None and made.tzinfo is None:     # SQLite gives them back without their zone
            made = made.replace(tzinfo=timezone.utc)
        if state == "pending" and approval != "requested" and made is not None and made + PROPOSAL_TTL < now:
            state = "expired"
        if state not in _CARD_STATES:
            continue
        old = history[i]
        history[i] = ChatMessage(role="tool", tool_call_id=old.tool_call_id, text=_json.dumps(
            {**body, "status": state, "next_steps": _CARD_STATES[state]}, default=str))
    return history


# ---------------------------------------------------------------------------
# Chat result
# ---------------------------------------------------------------------------


@dataclass
class ChatResult:
    session_id: str
    text: str
    proposals: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    turns: int = 1
    provider_shape: str = "anthropic"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_chat_turn(
    db: Session,
    *,
    user_id: str,
    username: str | None = None,
    user_message: str,
    session_id: str | None = None,
    ip_address: str | None = None,
    lang: str = "en",
    ocr_context: str | None = None,
    attachment_ids: list[str] | None = None,
    source_amounts: list[int] | None = None,
    registry: ToolRegistry | None = None,
    client: LLMClient | None = None,
    mode: str = "default",
    reply_to: dict | None = None,
) -> ChatResult:
    """Drive one user-message turn through the AI accountant agent loop.

    Returns when the model says ``end_turn`` (or we hit ``MAX_TURNS``). The
    return value lists every proposal registered during the turn so the
    frontend can render the action cards.

    ``lang`` is the user's preferred UI language; it localizes the
    fallback/status text and tells the model which language to answer in
    (AI-2). ``ocr_context`` is OCR text extracted from any attached
    document, injected into the turn; ``attachment_ids`` are linked onto
    whatever transaction the model proposes this turn (chat-attachment
    feature).

    ``client`` lets tests inject a mock; in production we look up the
    active shape via ``_resolve_chat_shape`` and instantiate the
    corresponding adapter.

    ``mode="personal"`` (personal-finance tenants) selects the trimmed
    personal tool registry and appends the personal-mode prompt addendum.

    ``reply_to`` is the message this one answers (``replies.quoted``): the
    model reads it ahead of the user's words, and it is kept beside them.
    """
    lang = _message_language(user_message, lang if lang in _LANG_NAMES else "en")
    attachment_ids = list(attachment_ids or [])
    # Source amounts for the proposal sanity guard: OCR'd document totals
    # passed in by the caller, plus any numbers in the user's own message.
    src_amounts = list(source_amounts or [])
    src_amounts.extend(_numbers_in_text(user_message))
    personal = mode == "personal"
    # a caller's own registry (a test's) is offered whole; AI_TOOL_ROUTING=false offers every tool
    from app.core.config import settings as _settings
    routed = registry is None and _settings.ai_tool_routing
    reg = registry or (build_personal_registry() if personal else build_default_registry())
    tool_defs = reg.to_anthropic()  # provider-neutral: {name, description, input_schema}

    # Give the model the REAL current date — it won't reliably read it from a
    # tool, and it judges "future" against its training-era clock otherwise
    # (rejecting valid past dates). Show the Jalali equivalent too when the
    # company displays the Jalali calendar.
    from datetime import date as _date

    from app.services.locale_service import get_display_calendar

    today = _date.today()
    today_str = today.isoformat()
    jalali_books = False
    try:
        if get_display_calendar(db) == "jalali":
            jalali_books = True
            from app.utils.jalali import format_jalali

            from app.utils.jalali import format_jalali_long

            today_str = f"{today.isoformat()} (Jalali {format_jalali(today)} = {format_jalali_long(today)})"
    except Exception:
        pass
    # The instructions and the company's standing lines first, the same on every call, so a provider's prompt
    # cache serves them (an OpenAI-shape gateway caches a repeated prefix by itself; the Anthropic client marks
    # it); today's date and this message's language last — the date sat at the top and changed the whole
    # prompt every day, and each message resent ~24k tokens of instructions and tools (2026-10-22).
    system_prompt = SYSTEM_PROMPT + (PERSONAL_MODE_ADDENDUM if personal else "")
    try:
        # the company's standing choices, learned from corrections (roadmap §5.4)
        from app.services.learned_preferences import prompt_block
        system_prompt += prompt_block(db)
    except Exception:  # noqa: BLE001 — never lose a chat turn over a hint
        logger.warning("learned preferences unavailable for the prompt", exc_info=True)
    if personal:
        try:
            # the person's places, categories and people: the model missed them searching (scenarios AY3–AY5)
            from app.services.ai_accountant.personal_glance import glance_block
            system_prompt += glance_block(db)
        except Exception:  # noqa: BLE001 — never lose a chat turn over a hint
            logger.warning("the books at a glance unavailable for the prompt", exc_info=True)
    try:
        # the books' own currency: «۸۶ ریال و ۱۰۰ بیسه» was taken as 86.1 Iranian rials in
        # personal books that count in IRR (scenario Z1, Z2)
        from app.core.currencies import name as currency_name
        from app.core.money import exponent
        from app.services.fx_base import base_currency
        cur = base_currency(db)
        system_prompt += (f"\n\nThese books count in {cur} ({currency_name(cur, 'en')}; "
                          f"amounts have {exponent(cur)} decimal places).")
        if personal:
            from sqlalchemy import func as _f, select as _sel

            from app.models.transaction import Transaction as _T
            has = db.execute(_sel(_f.count()).select_from(_T).where(_T.deleted_at.is_(None))).scalar()
            system_prompt += (" The person can still change it: the books have no entries yet." if not has else
                              " It is fixed now: the books already have entries.")
    except Exception:  # noqa: BLE001 — never lose a chat turn over a hint
        logger.warning("the books' currency unavailable for the prompt", exc_info=True)
    system_prompt += f"\n\nToday: {today_str}."
    # The language rule sits far up the prompt and lost to the Persian books:
    # "What was our biggest expense last month?" was answered in Persian (AI QA, 2026-10-12).
    system_prompt += f"\nThe interface language is {_LANG_NAMES[lang]}."
    system_prompt += f"\n\nThis turn: the user's message is in {_LANG_NAMES[lang]} — answer it in {_LANG_NAMES[lang]}."
    if lang != "fa":
        system_prompt += " Write figures with Latin digits and the currency code (843,110,379 IRR), and translate account names carefully (حقوق پایه is base salary)."

    shape = "anthropic"
    if client is None:
        shape = _resolve_chat_shape(db)
        client = _get_chat_client(shape)
    else:
        shape = getattr(client, "shape", "unknown")

    chat_session = _get_or_create_session(db, user_id=user_id, session_id=session_id)
    session_uuid_str = str(chat_session.id)

    history = _replay_history(db, chat_session.id)

    # Only the tools and the parts of the instructions this message needs (2026-10-22): the core, and the
    # groups its words and the exchanges just before it are about; ``more_tools`` adds any other.
    from app.services.ai_accountant import tool_routing as _routing
    all_defs = {d["name"]: d for d in tool_defs}
    groups: set[str] = set()

    def _offer(chosen: set[str]) -> list[dict[str, Any]]:
        return ([all_defs[n] for n in _routing.tool_names(chosen, set(all_defs))] + [_routing.more_tools_spec()])

    if routed:
        recent = [tc.name for m in history if m.role == "assistant" for tc in m.tool_calls]
        groups = _routing.route(user_message, recent_tools=recent, personal=personal,
                                attachments=bool(ocr_context or attachment_ids))
        tool_defs = _offer(groups)
        if system_prompt.startswith(SYSTEM_PROMPT):
            sections = groups | ({"reply"} if reply_to else set()) | ({"attachments"} if (ocr_context or attachment_ids) else set())
            system_prompt = _routing.instructions(SYSTEM_PROMPT, sections) + system_prompt[len(SYSTEM_PROMPT):]
    # Fold OCR context into the user's turn so the model can reason over the
    # attached document's fields. Persist the augmented text so a session
    # replay still carries the document context.
    turn_text = user_message
    if ocr_context:
        turn_text = (user_message or "").rstrip()
        if turn_text:
            turn_text += "\n\n"
        turn_text += ocr_context
    if reply_to:
        from app.services.ai_accountant import replies
        turn_text = replies.for_model(db, reply_to) + "\n\n" + (turn_text or "")
    user_turn = ChatMessage(role="user", text=turn_text)
    history.append(user_turn)
    stored = user_turn.to_dict()
    if ocr_context or reply_to:
        stored["said"] = user_message or ""         # what the user typed: "text" also carries the document, for the model
    if reply_to:
        stored["reply_to"] = reply_to
    if attachment_ids:
        stored["files"] = sent_files(db, attachment_ids)
    _persist_message(db, session_id=chat_session.id, role="user", content=stored)
    maybe_autotitle_session(db, chat_session, user_message)

    tool_ctx = ToolContext(
        db=db,
        user_id=user_id,
        username=username,
        chat_session_id=session_uuid_str,
        user_message=user_message,
        ip_address=ip_address,
        attachment_ids=attachment_ids,
        source_amounts=src_amounts,
        mode="personal" if personal else "default",
    )

    proposals: list[dict[str, Any]] = []
    tool_call_log: list[dict[str, Any]] = []
    # Most recent entity-resolution candidates, used to build a graceful
    # "pick one" partial if the turn budget is exhausted (AI-1).
    last_candidates: list[dict[str, Any]] = []
    from app.services.ai_accountant import guardrails
    from app.services.ai_usage import load_settings as _ai_limits
    _limits = _ai_limits(db)
    call_budget = int(_limits.get("tool_calls_per_message") or 60)
    proposal_budget = int(_limits.get("proposals_per_message") or 20)
    budget_spent = False
    final_text = ""
    stop_reason: str | None = None
    pause_attempts = 0
    refused_cards: list[str] = []
    unfixed: list[str] = []              # the tools whose refused card no later card of theirs replaced
    retried_refusal = False

    from app.services.ai_accountant import progress

    for turn in range(1, MAX_TURNS + 1):
        progress.emit("thinking", lang=lang)
        try:
            response = await client.chat(
                system_prompt=system_prompt,
                tools=tool_defs,
                messages=history,
            )
        except LLMClientError as e:
            raise AIAccountantError(str(e)) from e

        stop_reason = response.stop_reason
        assistant_msg = response.message
        # …also beside another card: the gym's card was refused in Iranian rials while the exchange's card stood,
        # and was never drafted again (eval, 2026-10-22, scenario AZ2)
        if (stop_reason == "end_turn" or not assistant_msg.tool_calls) and refused_cards and unfixed \
                and not retried_refusal:
            # A card was refused and the model ended the turn without one, telling
            # the user it was ready (AI QA on migrated books, 2026-10-12: «۵۰ میلیون
            # تومان» refused as tomans, then "the invoice is ready"). Hand it the
            # refusal once more; this reply is neither kept nor shown.
            retried_refusal = True
            history.append(assistant_msg)
            history.append(ChatMessage(role="user", text=(
                "[The card you drafted was refused: " + " ".join(refused_cards[-3:]) + " Draft the corrected card "
                "now, or tell the user plainly why you can't. Never say a card is ready when there is none.]")))
            continue
        history.append(assistant_msg)
        _persist_message(
            db, session_id=chat_session.id, role="assistant",
            content=assistant_msg.to_dict(),
        )

        if stop_reason == "pause_turn":
            pause_attempts += 1
            if pause_attempts > PAUSE_TURN_RETRIES:
                logger.warning("ai-accountant: pause_turn retries exhausted — bailing")
                break
            continue

        if budget_spent and assistant_msg.tool_calls:
            # Told the budget is spent and still calling: answer every call (a
            # call without its result breaks the next request) and stop here.
            for call in assistant_msg.tool_calls:
                trm = ChatMessage(role="tool", tool_call_id=call.id, is_error=True,
                                  text="Tool budget for this message is used up.")
                history.append(trm)
                _persist_message(db, session_id=chat_session.id, role="tool", content=trm.to_dict())
            _collapse_redundant_entity_proposals(db, proposals, 0, user_message)
            _people_on_every_card(db, proposals, personal=personal, user_message=user_message)
            _named_place_on_cards(db, proposals, personal=personal, user_message=user_message)
            return ChatResult(
                session_id=session_uuid_str,
                text=(assistant_msg.text or "").strip() or _status(lang, "tool_budget"),
                proposals=proposals, tool_calls=tool_call_log, stop_reason="tool_budget",
                turns=turn, provider_shape=shape,
            )

        if stop_reason == "end_turn" or not assistant_msg.tool_calls:
            final_text = _no_stray_tail(_true_figures(assistant_msg.text or "", tool_call_log))
            final_text = _choices_as_chips(final_text, tool_call_log)
            # Final one-card pass over EVERY proposal raised this chat turn (the
            # standalone create and the transaction/time action are often in
            # different LLM turns), so a redundant standalone create is dropped.
            _collapse_redundant_entity_proposals(db, proposals, 0, user_message)
            _people_on_every_card(db, proposals, personal=personal, user_message=user_message)
            _named_place_on_cards(db, proposals, personal=personal, user_message=user_message)
            return ChatResult(
                session_id=session_uuid_str,
                text=final_text,
                proposals=proposals,
                tool_calls=tool_call_log,
                stop_reason=stop_reason,
                turns=turn,
                provider_shape=shape,
            )

        # Execute every tool the model asked for and feed results back.
        tool_result_messages: list[ChatMessage] = []
        proposals_before_turn = len(proposals)
        for call in assistant_msg.tool_calls:
            if call.name == "more_tools":
                # the model asked for a group it wasn't offered: it has it from the next call on
                asked = {g for g in (call.input or {}).get("groups") or [] if g in _routing.GROUPS}
                groups |= asked
                tool_defs = _offer(groups)
                added = [n for g in asked for n in _routing.GROUPS[g][1] if n in all_defs]
                tool_result_messages.append(ChatMessage(role="tool", tool_call_id=call.id, text=(
                    f"Added: {', '.join(added) or 'nothing new'}. Use them now." if added else
                    "No such group; the groups are listed in this tool's description.")))
                continue
            log_entry = {"tool_use_id": call.id, "name": call.name, "input": call.input}
            tool_call_log.append(log_entry)
            tool = reg.get(call.name)
            if len(tool_call_log) > call_budget:
                budget_spent = True
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id, is_error=True,
                    text=(f"Tool budget for this message is used up ({call_budget} calls). Don't call more "
                          "tools: answer the user with what you have and say what is left to do."),
                ))
                continue
            if tool is not None and tool.category == "proposal" and len(proposals) >= proposal_budget:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id, is_error=True,
                    text=(f"{proposal_budget} proposals are waiting in this message already — that's the limit. "
                          "Tell the user to confirm or cancel those first, then continue."),
                ))
                continue
            if tool is None:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Unknown tool: {call.name!r}", is_error=True,
                ))
                continue

            # Detect malformed arguments from weak local models.
            if call.input.get("_parse_error"):
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Tool call had malformed JSON arguments: "
                         f"{call.input.get('_raw_arguments', '')!r}. Retry with valid JSON.",
                    is_error=True,
                ))
                continue

            try:
                args = tool.InputSchema.model_validate(_ascii_digits(_gregorian_dates(call.input)))
            except ValidationError as e:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Invalid input: {e}", is_error=True,
                ))
                continue

            progress.emit("tool", tool=call.name, lang=lang)
            try:
                result = await tool.run(tool_ctx, args)
            except ToolError as e:
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Tool error ({e.code}): {e.message}", is_error=True,
                ))
                if tool.category == "proposal" and e.code in _CORRECTABLE_REFUSALS:
                    refused_cards.append(e.message)
                    unfixed.append(call.name)
                continue
            except Exception as e:
                logger.exception("ai-accountant: tool %s crashed", call.name)
                tool_result_messages.append(ChatMessage(
                    role="tool", tool_call_id=call.id,
                    text=f"Internal tool error: {type(e).__name__}: {e}", is_error=True,
                ))
                continue

            if tool.category == "proposal" and isinstance(result, dict) and "confirmation_token" in result:
                try:
                    result.update(guardrails.review(db, result))
                except guardrails.ProposalRefused as e:
                    tool_result_messages.append(ChatMessage(
                        role="tool", tool_call_id=call.id,
                        text=f"Refused (period_locked): {e}", is_error=True,
                    ))
                    continue

            import json as _json
            tool_result_messages.append(ChatMessage(
                role="tool", tool_call_id=call.id,
                text=_json.dumps(_for_the_model(result, jalali=jalali_books, persian=lang == "fa"), default=str),
            ))
            log_entry["result"] = result
            if tool.category == "proposal" and isinstance(result, dict) and "confirmation_token" in result:
                replaced = _withdraw_replaced(db, getattr(args, "replaces", None), user_id=user_id,
                                              session_id=tool_ctx.chat_session_id, tool_name=call.name,
                                              new_token=result["confirmation_token"])
                if replaced:
                    result["replaces"] = replaced        # the client marks the old card as changed
                result.setdefault("tool_name", call.name)    # a tool that didn't name itself: its card still says what it is
                proposals.append(result)
                if call.name in unfixed:
                    unfixed.remove(call.name)
            # Remember entity candidates so we can offer a "pick one" partial
            # if the model never converges before MAX_TURNS (AI-1).
            if call.name in ("find_entity", "list_entities") and isinstance(result, dict):
                cands = result.get("matches") or result.get("entities") or []
                if cands:
                    last_candidates = cands

        # One-card UX: if this turn emitted BOTH a standalone propose_create_entity
        # AND a propose_create_transaction that already folds the same party in via
        # new_entities, drop the standalone (the model sometimes ignores the prompt
        # rule). The combined card creates + links the party in one Confirm.
        _collapse_redundant_entity_proposals(db, proposals, proposals_before_turn, user_message)

        # Append each tool result as its own ChatMessage (the wire-format
        # adapter batches them appropriately for Anthropic / spreads them
        # for OpenAI).
        for trm in tool_result_messages:
            history.append(trm)
            _persist_message(
                db, session_id=chat_session.id, role="tool",
                content=trm.to_dict(),
            )

    logger.warning(
        "ai-accountant: hit MAX_TURNS=%d for session %s — returning partial result",
        MAX_TURNS, session_uuid_str,
    )
    # Graceful partial instead of a dead-end (AI-1). If the model was stuck
    # resolving an entity, surface the candidates it found and ask the user
    # to pick. Otherwise ask for a little more detail. Localized (AI-2).
    if final_text:
        fallback_text = final_text
    elif last_candidates:
        listed = "\n".join(
            f"  • {c.get('name', '?')}"
            + (f" ({c.get('type')})" if c.get("type") else "")
            for c in last_candidates[:5]
        )
        fallback_text = _status(lang, "max_turns_candidates").format(candidates=listed)
    else:
        fallback_text = _status(lang, "max_turns_dead_end")
    _people_on_every_card(db, proposals, personal=personal, user_message=user_message)
    return ChatResult(
        session_id=session_uuid_str,
        text=fallback_text,
        proposals=proposals,
        tool_calls=tool_call_log,
        stop_reason=stop_reason,
        turns=MAX_TURNS,
        provider_shape=shape,
    )
