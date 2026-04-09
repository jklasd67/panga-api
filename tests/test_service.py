from decimal import Decimal

from app.service import to_decimal_amount


def test_amount_precision():
    assert to_decimal_amount("100.00") == Decimal("100.00")


def test_amount_rounding():
    assert to_decimal_amount("100.005") == Decimal("100.01")
