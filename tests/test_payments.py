from collections.abc import Iterator
from typing import Any

import pytest

from commerce_engine.money import Money
from commerce_engine.payments import PaymentGateway


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    saved = dict(PaymentGateway._registry)
    yield
    PaymentGateway._registry.clear()
    PaymentGateway._registry.update(saved)


def test_register_and_lookup() -> None:
    class Stripe(PaymentGateway, code="stripe"):
        def charge(self, amount: Money) -> str:
            return f"charged {amount!r}"

    assert PaymentGateway.for_code("stripe") is Stripe
    assert Stripe.code == "stripe"


def test_missing_code_is_rejected_by_the_signature() -> None:
    with pytest.raises(TypeError, match="code"):

        class NoCode(PaymentGateway):  # type: ignore[call-arg]
            def charge(self) -> None: ...

    assert "code" not in PaymentGateway._registry


def test_code_is_keyword_only_without_default() -> None:
    import inspect

    param = inspect.signature(PaymentGateway.__init_subclass__).parameters["code"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


@pytest.mark.parametrize("charge", [None, "not callable", 42])
def test_non_callable_charge_is_rejected(charge: Any) -> None:
    with pytest.raises(TypeError, match="charge"):
        type("Bad", (PaymentGateway,), {"charge": charge}, code="bad")
    assert "bad" not in PaymentGateway._registry


def test_missing_charge_is_rejected() -> None:
    with pytest.raises(TypeError, match="charge"):

        class Empty(PaymentGateway, code="empty"):
            pass


def test_inherited_charge_counts() -> None:
    class Stripe(PaymentGateway, code="stripe"):
        def charge(self) -> None: ...

    class StripeEU(Stripe, code="stripe-eu"):
        pass

    assert PaymentGateway.for_code("stripe-eu") is StripeEU
    assert PaymentGateway.for_code("stripe") is Stripe


def test_duplicate_code_names_both_classes() -> None:
    class Stripe(PaymentGateway, code="stripe"):
        def charge(self) -> None: ...

    with pytest.raises(TypeError) as excinfo:

        class Impostor(PaymentGateway, code="stripe"):
            def charge(self) -> None: ...

    assert "Stripe" in str(excinfo.value)
    assert "Impostor" in str(excinfo.value)
    assert PaymentGateway.for_code("stripe") is Stripe


def test_cooperative_chaining_with_a_mixin() -> None:
    seen: list[tuple[str, str]] = []

    class Tagged:
        def __init_subclass__(cls, *, tag: str = "", **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            seen.append((cls.__name__, tag))

    class Adyen(PaymentGateway, Tagged, code="adyen", tag="eu"):
        def charge(self) -> None: ...

    assert seen == [("Adyen", "eu")]
    assert PaymentGateway.for_code("adyen") is Adyen


def test_unknown_code_is_value_error() -> None:
    with pytest.raises(ValueError, match="nope"):
        PaymentGateway.for_code("nope")
