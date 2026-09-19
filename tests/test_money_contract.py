import copy
import pickle
from typing import Any

import pytest

from commerce_engine.money import CardPayment, Currency, PaymentMethod


def test_identity_is_case_insensitive() -> None:
    assert Currency("usd") is Currency("USD")
    assert Currency("Usd") is Currency("uSD")


def test_distinct_codes_are_distinct_objects() -> None:
    assert Currency("USD") is not Currency("EUR")


def test_interning_happens_in_new() -> None:
    assert Currency.__new__(Currency, "usd") is Currency.__new__(Currency, "USD")


def test_reconstruction_does_not_reinitialise_a_cached_instance() -> None:
    first = Currency("BHD")
    original = first.code
    object.__setattr__(first, "code", "PLANTED")
    try:
        second = Currency("bhd")
        assert second is first
        assert first.code == "PLANTED", "re-construction re-ran initialisation"
    finally:
        object.__setattr__(first, "code", original)


def test_calling_init_directly_does_not_reset_state() -> None:
    usd = Currency("USD")
    usd.__init__("EUR")  # type: ignore[misc]  # noqa: PLC2801
    assert usd.code == "USD"
    assert usd.name == "US Dollar"
    assert usd.minor_units == 2


def test_instances_are_immutable() -> None:
    usd = Currency("USD")
    with pytest.raises(AttributeError):
        usd.code = "EUR"
    assert usd.code == "USD"
    assert Currency("USD").code == "USD"


def test_copy_preserves_identity() -> None:
    usd = Currency("USD")
    assert copy.copy(usd) is usd
    assert copy.deepcopy(usd) is usd


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickle_preserves_identity(protocol: int) -> None:
    usd = Currency("USD")
    assert pickle.loads(pickle.dumps(usd, protocol)) is usd


def test_copy_and_pickle_preserve_identity_when_nested() -> None:
    order = {"currency": Currency("USD"), "lines": [Currency("usd")]}
    restored = pickle.loads(pickle.dumps(copy.deepcopy(order)))
    assert restored["currency"] is Currency("USD")
    assert restored["lines"][0] is Currency("USD")


def test_unpickling_does_not_bypass_validation() -> None:
    payload = pickle.dumps(Currency("USD"))
    assert b"XYZ" not in payload
    assert pickle.loads(payload) is Currency("USD")


def test_factory_returns_the_right_subclass() -> None:
    method = PaymentMethod("card", last4="4242")
    assert type(method) is CardPayment
    assert method.last4 == "4242"


def test_factory_runs_the_subclass_init_exactly_once() -> None:
    calls: list[tuple[str, str]] = []

    class CountedCard(CardPayment, kind="counted-card"):
        def __init__(self, last4: str, holder: str = "") -> None:
            calls.append((last4, holder))
            super().__init__(last4, holder)

    try:
        method = PaymentMethod("counted-card", last4="4242", holder="A. Ranjbar")
        assert isinstance(method, CountedCard)
        assert calls == [("4242", "A. Ranjbar")]
        assert method.last4 == "4242"
        assert method.holder == "A. Ranjbar"
    finally:
        PaymentMethod._registry.pop("counted-card", None)


def test_subclass_init_is_not_called_with_the_factory_arguments() -> None:
    method = PaymentMethod("card", last4="4242")
    assert isinstance(method, CardPayment)
    assert method.last4 == "4242"
    assert method.holder == ""


def test_subclass_validation_still_runs_through_the_factory() -> None:
    with pytest.raises(ValueError, match="4 digits"):
        PaymentMethod("card", last4="42")


def test_missing_subclass_argument_reports_the_subclass_signature() -> None:
    with pytest.raises(TypeError, match="last4"):
        PaymentMethod("card")


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="crypto"):
        PaymentMethod("crypto", address="bc1")


def test_unknown_kind_is_rejected_by_new() -> None:
    with pytest.raises(ValueError):
        PaymentMethod.__new__(PaymentMethod, "crypto", address="bc1")

    with pytest.raises(ValueError) as exc_info:
        PaymentMethod("crypto", address="bc1")
    assert exc_info.traceback[-1].name == "__new__"


def test_dispatch_must_happen_in_new_not_init() -> None:
    method = PaymentMethod.__new__(PaymentMethod, "card", last4="4242")
    assert type(method) is CardPayment, (
        "PaymentMethod.__new__ did not dispatch; the factory logic has moved "
        "somewhere that cannot choose the class"
    )


def test_base_class_is_never_the_result() -> None:
    cases: list[tuple[str, dict[str, Any]]] = [
        ("card", {"last4": "4242"}),
        ("cash", {"tendered_cents": 500}),
        ("wallet", {"provider": "applepay", "wallet_id": "w-1"}),
    ]
    for kind, kwargs in cases:
        method = PaymentMethod(kind, **kwargs)
        assert type(method) is not PaymentMethod
        assert isinstance(method, PaymentMethod)
