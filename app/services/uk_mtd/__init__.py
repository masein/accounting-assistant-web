"""UK Making Tax Digital (roadmap 2026-09 §3.6).

* ``periods`` — tax years, the ITSA quarterly update periods (standard or
  calendar basis) and VAT return periods (the three quarterly staggers or
  monthly), with HMRC's deadlines.
* ``settings`` — per-company choices (income source, period basis, VAT
  registration and stagger, account → HMRC category overrides).
* ``vat`` — VAT return boxes 1–9 from the period's invoices, as the MTD VAT
  API body.
* ``itsa`` — quarterly self-employment / UK property figures in HMRC's
  categories from the ledger (added with the ITSA work).

Export first: the figures and request bodies match HMRC's published API
schemas so bridging software (or a later direct submission) can use them.
"""
