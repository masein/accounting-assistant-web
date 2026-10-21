# Each currency as it is, everywhere in a person's books

The owner, 2026-10-21: «i want to have different currencies. isn't that ok?» Yes, it is. A person keeps rials at a bank, Omani rials in a wallet and dollars in a vault, and wants each kept as it is (#371, scenarios AB). The chat already says each currency on its own: cash (#371), people (#377) and spending (#393, scenarios AO).

The rest of a person's books still totals everything in the books' currency at its base value. A line with no exchange rate has no base value, so Omani rials in rial books read as nothing in:

| Where | What | Reads |
|---|---|---|
| Weekly and monthly reviews (chat card, phone, bell) | spent, income, categories, biggest spend, bars, saved, savings rate | `report_card._flows`: base sums; `_biggest`: `base_debit > 0` |
| Monthly report card (My finances, `get_report_card`) | the same, plus budgets, net worth and goals | `_flows`, `budget_utilization`, `compute_net_worth` |
| My finances (web) | cash, spent this month, saved, categories, trend | `/reports/owner-dashboard`: the books' currency only, so OMR entries are filtered out |
| Phone widget | cash, budget left | `/summary`: the base total only |
| Net worth, savings goals | assets, debts, a goal's progress | base sums: a goal on an OMR wallet reads 0% |
| Budgets and their alerts | spent against the limit | base sums; a budget has no currency |
| Can I afford it?, insights, subscriptions | cash, average month, spikes, runway, recurring payments | base sums; subscriptions read the books' currency only |

**Rule for a person's books:** every figure is per currency, in that currency's own amounts. Currencies are never added together and never exchanged, even when rates are on file. The books' currency comes first, then the others by how much they move. Company books keep their base-currency totals (roadmap §4.6), with the other currencies listed beside them as in #393.

## Phases (one PR each, scenarios first)

All five are done, on 2026-10-21: #394 (AP), #395 (AQ), #396 (AR), #397 (AS) and the last one (AT).

1. **Reviews and the report card.**
   - `_flows` per currency.
   - A personal review carries each currency's facts; the chat shows one card per currency (the phone already draws each review block), and the headline and bell come from the first.
   - The report card and `get_report_card` gain `by_currency`.
   - Suggestions stay with the first currency.
2. **My finances and the widget.**
   - The page gets a KPI row and categories per currency, plus a trend for the currency chosen.
   - `/summary` gives cash per currency. The widget lists them, which needs the next APK.
3. **Net worth and savings goals.**
   - Net worth per currency.
   - A goal counts in its account's currency: target, progress, monthly need.
4. **Budgets.**
   - A budget has a currency (migration, defaulting to the books'). Its actual reads that currency's own amounts.
   - Alerts and `propose_set_budget` follow.
5. **Can I afford it?, insights, subscriptions.**
   - Priced and judged in the price's own currency, against that currency's cash and months.
   - Insights and recurring payments per currency.
