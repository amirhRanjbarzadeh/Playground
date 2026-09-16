from decimal import Decimal
from typing import Any

import pytest

from commerce_platform import Product


def test_initial_price_round_trip() -> None:
    product = Product("Test Product", 1050)

    assert product._price_cents == 1050
    assert product.price == Decimal("10.50")
    assert product.price_display == "$10.50"


@pytest.mark.parametrize(
    ("value", "expected_cents", "expected_price", "expected_display"),
    [
        (56.78, 5678, Decimal("56.78"), "$56.78"),
        (Decimal("10.50"), 1050, Decimal("10.50"), "$10.50"),
        (10, 1000, Decimal("10.00"), "$10.00"),
        (0, 0, Decimal("0.00"), "$0.00"),
    ],
)
def test_setting_valid_price(
    value: int | float | Decimal,
    expected_cents: int,
    expected_price: Decimal,
    expected_display: str,
) -> None:
    product = Product("Test Product", 0)

    product.price = value

    assert product._price_cents == expected_cents
    assert product.price == expected_price
    assert product.price_display == expected_display


def test_price_accepts_extra_decimal_trailing_zeros() -> None:
    product = Product("Test Product", 0)

    product.price = Decimal("10.500")

    assert product._price_cents == 1050
    assert product.price == Decimal("10.50")
    assert product.price_display == "$10.50"


def test_price_rejects_half_cent() -> None:
    product = Product("Test Product", 0)

    with pytest.raises(ValueError, match="sub-cent"):
        product.price = Decimal("0.005")


@pytest.mark.parametrize(
    "value",
    [
        -1,
        -10.50,
        Decimal("-0.01"),
    ],
)
def test_price_rejects_negative_values(value: int | float | Decimal) -> None:
    product = Product("Test Product", 0)

    with pytest.raises(ValueError, match="negative"):
        product.price = value


@pytest.mark.parametrize(
    "value",
    [
        "10.50",
        None,
        [],
        {},
        object(),
    ],
)
def test_price_rejects_non_numeric_values(value: Any) -> None:
    product = Product("Test Product", 0)

    with pytest.raises(ValueError, match="number"):
        product.price = value


def test_setting_price_replaces_previous_price() -> None:
    product = Product("Test Product", 1050)

    product.price = Decimal("25.75")

    assert product._price_cents == 2575
    assert product.price == Decimal("25.75")
    assert product.price_display == "$25.75"
