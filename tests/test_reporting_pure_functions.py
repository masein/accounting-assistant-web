from __future__ import annotations

import unittest

from app.models.inventory import InventoryMovementType
from app.services.reporting.common import ASSET, LIABILITY, REVENUE, balance_from_turnovers
from app.services.reporting.financial_statement_service import classify_cash_flow_activity
from app.services.inventory_costing import FIFO, WEIGHTED_AVERAGE, run_costing


def _cost(moves, method=WEIGHTED_AVERAGE):
    """[(kind, qty, unit_cost)] for one item → its ItemState."""
    from types import SimpleNamespace
    rows = [SimpleNamespace(id=i, item_id="x", movement_type=getattr(InventoryMovementType, k).value,
                            quantity=q, unit_cost=c) for i, (k, q, c) in enumerate(moves)]
    return run_costing(rows, method)[0]["x"]


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
        acc = _cost([("IN", 10, 100), ("IN", 10, 200)])
        self.assertEqual(float(acc.on_hand), 20.0)
        self.assertEqual(acc.unit_cost, 150)
        acc = _cost([("IN", 10, 100), ("IN", 10, 200), ("OUT", 4, 0)])
        self.assertEqual(float(acc.on_hand), 16.0)
        self.assertEqual(acc.cogs, 600)

    def test_on_hand_does_not_depend_on_same_day_order(self):
        """OUT 4 then IN 10 used to clamp to 0 first and end at 10."""
        for method in (WEIGHTED_AVERAGE, FIFO):
            for order in ([("IN", 10, 100), ("OUT", 4, 100)], [("OUT", 4, 100), ("IN", 10, 100)]):
                self.assertEqual(float(_cost(order, method).on_hand), 6.0)

    def test_overselling_shows_as_negative_stock(self):
        for method in (WEIGHTED_AVERAGE, FIFO):
            acc = _cost([("IN", 2, 100), ("OUT", 5, 100)], method)
            self.assertEqual(float(acc.on_hand), -3.0)
            self.assertEqual(acc.value, 0)


if __name__ == "__main__":
    unittest.main()
