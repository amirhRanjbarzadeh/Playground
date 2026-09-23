import pytest

from commerce_platform.order import Order


def test_setting_public() -> None:
    order = Order([], 0)
    order.place()

    with pytest.raises(AttributeError, match="cannot modify"):
        order.items = []


def test_setting_private() -> None:
    order = Order([], 0)
    order.place()

    # Setting an attribute the class never declares is the whole point of
    # this test: __setattr__ lets private names through after place().
    order._note = "test"

    assert order._note == "test"  # type: ignore[attr-defined]
