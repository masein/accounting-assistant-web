from __future__ import annotations

import unittest

from app.models.inventory import InventoryMovementType
from app.services.reporting.common import ASSET, LIABILITY, REVENUE, balance_from_turnovers
from app.services.reporting.financial_statement_service import classify_cash_flow_activity
from app.services.reporting.inventory_report_service import ItemAccumulator, apply_inventory_movement


class ReportingPureFunctionTests(unittest.TestCase):
    def test_balance_from_turnovers_by_account_nature(self):
        self.assertEqual(balance_from_turnovers(ASSET, 1000, 300), 700)
        self.assertEqual(balance_from_turnovers(LIABILITY, 1000, 300), -700)
        self.assertEqual(balance_from_turnovers(REVENUE, 100, 450), 350)

    def test_cash_flow_classifier(self):
        self.assertEqual(classify_cash_flow_activity(["1210"], [ASSET]), "investing")
        self.assertEqual(classify_cash_flow_activity(["3110"], [LIABILITY]), "financing")
        self.assertEqual(classify_cash_flow_activity(["6110"], [REVENUE]), "operating")

    def test_weighted_average_inventory(self):
        acc = ItemAccumulator()
        apply_inventory_movement(acc, InventoryMovementType.IN.value, 10, 100)
        apply_inventory_movement(acc, InventoryMovementType.IN.value, 10, 200)
        self.assertAlmostEqual(acc.on_hand, 20.0)
        self.assertEqual(acc.avg_cost, 150)
        apply_inventory_movement(acc, InventoryMovementType.OUT.value, 4, 0)
        self.assertAlmostEqual(acc.on_hand, 16.0)
        self.assertEqual(acc.cogs, 600)


if __name__ == "__main__":
    unittest.main()
