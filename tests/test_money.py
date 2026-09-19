import copy
import pickle
import threading

import pytest

from commerce_engine.money import (
    CardPayment,
    CashPayment,
    Currency,
    PaymentMethod,
    WalletPayment,
)

def test_same_code_is_same_object() -> None:
    assert Currency("USD") is Currency("USD")


def test_lookup_is_case_insensitive_and_whitespace_tolerant() -> None:
    usd = Currency("USD")
    assert Currency("usd") is usd
    assert Currency("uSd") is usd
    assert Currency("  usd  ") is usd


def test_different_codes_are_different_objects() -> None:
    assert Currency("USD") is not Currency("EUR")


def test_attributes_are_populated() -> None:
    usd = Currency("usd")
    assert usd.code == "USD"
    assert usd.name == "US Dollar"
    assert usd.minor_units == 2
    assert Currency("JPY").minor_units == 0
    assert Currency("KWD").minor_units == 3


def test_repr_and_str() -> None:
    assert repr(Currency("USD")) == "Currency('USD')"
    assert str(Currency("USD")) == "USD"


def test_concurrent_construction_yields_one_object() -> None:
    results: list[Currency] = []
    barrier = threading.Barrier(8)

    def build() -> None:
        barrier.wait()
        results.append(Currency("nok"))

    threads = [threading.Thread(target=build) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8
    assert all(item is results[0] for item in results)


def test_invalid_code_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not a known ISO 4217"):
        Currency("XYZ")


def test_invalid_code_is_not_cached() -> None:
    with pytest.raises(ValueError):
        Currency("ZZZ")
    with pytest.raises(ValueError):
        Currency("zzz")


def test_validation_runs_in_new_not_init() -> None:
    # If validation lived in __init__, __new__ would already have produced an
    # object; here the allocation itself is what fails.
    with pytest.raises(ValueError):
        Currency.__new__(Currency, "XYZ")


def test_non_string_code_raises_type_error() -> None:
    with pytest.raises(TypeError):
        Currency(840)  # type: ignore[arg-type]

def test_cannot_rebind_existing_attribute() -> None:
    usd = Currency("USD")
    with pytest.raises(AttributeError):
        usd.code = "EUR"
    assert usd.code == "USD"


def test_cannot_add_new_attribute() -> None:
    usd = Currency("USD")
    with pytest.raises(AttributeError):
        usd.symbol = "$"


def test_cannot_delete_attribute() -> None:
    usd = Currency("USD")
    with pytest.raises(AttributeError):
        del usd.code


def test_has_no_instance_dict() -> None:
    assert not hasattr(Currency("USD"), "__dict__")


def test_copy_preserves_identity() -> None:
    usd = Currency("USD")
    assert copy.copy(usd) is usd


def test_deepcopy_preserves_identity() -> None:
    usd = Currency("USD")
    assert copy.deepcopy(usd) is usd
    assert copy.deepcopy({"price_currency": usd})["price_currency"] is usd


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickle_round_trip_preserves_identity(protocol: int) -> None:
    usd = Currency("USD")
    assert pickle.loads(pickle.dumps(usd, protocol)) is usd


def test_pickle_round_trip_inside_a_container() -> None:
    pair = (Currency("USD"), Currency("EUR"))
    restored = pickle.loads(pickle.dumps(pair))
    assert restored[0] is pair[0]
    assert restored[1] is pair[1]


def test_currency_is_hashable_and_usable_as_a_key() -> None:
    assert {Currency("USD"): 1}[Currency("usd")] == 1

def test_factory_returns_card_payment() -> None:
    method = PaymentMethod("card", last4="4242", holder="A. Ranjbar")
    assert type(method) is CardPayment
    assert method.last4 == "4242"
    assert method.holder == "A. Ranjbar"


def test_factory_returns_cash_payment() -> None:
    method = PaymentMethod("cash", tendered_cents=500)
    assert type(method) is CashPayment
    assert method.tendered_cents == 500


def test_factory_returns_wallet_payment() -> None:
    method = PaymentMethod("wallet", provider="ApplePay", wallet_id="w-1")
    assert type(method) is WalletPayment
    assert method.provider == "applepay"
    assert method.wallet_id == "w-1"
    assert method.token == ""


def test_factory_kind_is_case_insensitive() -> None:
    assert type(PaymentMethod("CARD", last4="4242")) is CardPayment
    assert type(PaymentMethod(" Cash ", tendered_cents=0)) is CashPayment


def test_unknown_kind_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown payment kind"):
        PaymentMethod("crypto", address="x")


def test_missing_kind_raises_type_error() -> None:
    with pytest.raises(TypeError):
        PaymentMethod()


def test_factory_does_not_leak_kind_into_the_subclass_init() -> None:
    method = PaymentMethod("card", last4="4242", holder="A. Ranjbar")
    assert not hasattr(method, "kind_arg")
    assert method.kind == "card"


def test_subclass_init_runs_exactly_once() -> None:
    calls: list[tuple[object, ...]] = []

    class Voucher(PaymentMethod, kind="voucher"):
        __slots__ = ("serial",)

        def __init__(self, serial: str) -> None:
            calls.append((serial,))
            self.serial = serial

    try:
        voucher = PaymentMethod("voucher", serial="V-1")
        assert calls == [("V-1",)]
        assert isinstance(voucher, Voucher)
        assert voucher.serial == "V-1"
    finally:
        del PaymentMethod._registry["voucher"]


def test_subclass_validation_still_fires_through_the_factory() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        PaymentMethod("cash", tendered_cents=-1)
    with pytest.raises(ValueError, match="4 digits"):
        PaymentMethod("card", last4="abcd")


def test_missing_subclass_argument_reports_the_subclass_signature() -> None:
    with pytest.raises(TypeError, match="last4"):
        PaymentMethod("card")


def test_unexpected_subclass_argument_is_rejected() -> None:
    with pytest.raises(TypeError):
        PaymentMethod("cash", tendered_cents=1, currency="USD")


def test_subclasses_are_directly_constructible() -> None:
    card = CardPayment(last4="4242", holder="  A. Ranjbar ")
    assert card.last4 == "4242"
    assert card.holder == "A. Ranjbar"
    assert CashPayment(250).tendered_cents == 250


def test_subclass_instances_are_payment_methods() -> None:
    assert isinstance(PaymentMethod("cash", tendered_cents=1), PaymentMethod)
    assert issubclass(CardPayment, PaymentMethod)


def test_card_repr_shows_only_last4() -> None:
    card = CardPayment(last4="4242", holder="A. Ranjbar")
    assert repr(card) == "CardPayment(last4='4242', holder='A. Ranjbar')"


def test_registry_rejects_a_duplicate_kind() -> None:
    with pytest.raises(ValueError, match="already registered"):

        class Rogue(PaymentMethod, kind="card"):
            pass


def test_unregistered_subclass_is_not_reachable_from_the_factory() -> None:
    class Invoice(PaymentMethod):
        __slots__ = ("reference",)

        def __init__(self, reference: str) -> None:
            self.reference = reference

    assert Invoice("INV-1").reference == "INV-1"
    with pytest.raises(ValueError):
        PaymentMethod("invoice", reference="INV-1")


@pytest.mark.parametrize(
    "method",
    [
        CardPayment(last4="4242", holder="A. Ranjbar"),
        CashPayment(tendered_cents=500),
        WalletPayment(provider="applepay", wallet_id="w-1", token="t"),
    ],
)
def test_payment_methods_pickle_round_trip(method: PaymentMethod) -> None:
    restored = pickle.loads(pickle.dumps(method))
    assert type(restored) is type(method)
    assert repr(restored) == repr(method)


def test_payment_method_copy_round_trip() -> None:
    card = PaymentMethod("card", last4="4242", holder="A. Ranjbar")
    clone = copy.deepcopy(card)
    assert clone is not card
    assert repr(clone) == repr(card)
