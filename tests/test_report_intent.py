from __future__ import annotations

import unittest
from datetime import date

from app.services.reporting.report_intent import parse_report_intent


class ReportIntentTests(unittest.TestCase):
    def test_parse_balance_sheet_last_month(self):
        intent = parse_report_intent("Show me balance sheet for last month", today=date(2026, 2, 22))
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.key, "balance_sheet")
        self.assertEqual(intent.from_date, date(2026, 1, 1))
        self.assertEqual(intent.to_date, date(2026, 1, 31))

    def test_parse_bank_statement_persian_bank_name_cleanup(self):
        intent = parse_report_intent("گردش بانک ملی هم میخوام", today=date(2026, 2, 22))
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.key, "account_ledger")
        self.assertEqual(intent.bank_name, "ملی")

    def test_parse_bank_statement_english_bank_name_cleanup(self):
        intent = parse_report_intent("show me bank statement for melli bank", today=date(2026, 2, 22))
        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.key, "account_ledger")
        self.assertEqual(intent.bank_name, "Melli")


if __name__ == "__main__":
    unittest.main()
