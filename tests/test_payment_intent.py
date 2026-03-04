"""Tests for payment vs receipt intent detection."""
from __future__ import annotations

import pytest

from app.services.transaction_fee import is_payment_intent


def _check(text: str) -> bool:
    return is_payment_intent([{"role": "user", "content": text}])


class TestPaymentDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "paid 5M from melli bank",
            "I paid rent 500000",
            "payment to Ali Roshan",
            "پرداخت 5 میلیون از بانک ملی",
            "paid employee salary",
        ],
    )
    def test_payment_phrases(self, text):
        assert _check(text) is True, f"Should be payment: {text!r}"


class TestReceiptDetection:
    @pytest.mark.parametrize(
        "text",
        [
            "received 2M from client",
            "client paid us 5M",
            "دریافت از مشتری",
        ],
    )
    def test_receipt_phrases(self, text):
        assert _check(text) is False, f"Should NOT be payment: {text!r}"


class TestThirdPartyDeposit:
    def test_x_paid_to_our_bank(self):
        assert _check("Nikzade payed to Mellat bank 36M") is False
