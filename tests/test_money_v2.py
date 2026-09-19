import copy
import pickle
import threading
import time
from decimal import ROUND_HALF_UP, Decimal

import pytest

from commerce_engine.money import Currency, CurrencyMismatch, Money


class Crypto(Currency):
    pass


class USDMoney(Money):
    __slots__ = ()


# --- acceptance -------------------------------------------------------------


def test_acceptance() -> None:
    assert Currency("usd") is Currency("USD")
    assert Crypto("USD") is not Currency("USD") and type(Crypto("USD")) is Crypto
    assert pickle.loads(pickle.dumps(Currency("JPY"))) is Currency("JPY")
    assert Money("19.99", "usd") + Money(1, "USD") == Money("20.99", "USD")
    assert Money.rounded("19.995", "USD").amount == Decimal("20.00")
    assert type(USDMoney(1, "USD") * 3) is USDMoney
    assert hash(Money(1, "USD")) == hash(Money("1.00", "USD"))


# --- Currency ---------------------------------------------------------------


def test_currency_has_no_init_and_exact_slots() -> None:
    assert "__init__" not in Currency.__dict__
    assert Currency.__slots__ == ("code", "minor", "__weakref__")
    assert not hasattr(Currency("USD"), "__dict__")


def test_minor_units_seeded() -> None:
    minors = {c: Currency(c).minor for c in ("USD", "EUR", "JPY", "KWD", "IRR")}
    assert minors == {"USD": 2, "EUR": 2, "JPY": 0, "KWD": 3, "IRR": 0}


def test_unknown_code_is_value_error() -> None:
    with pytest.raises(ValueError, match="XXX"):
        Currency("XXX")


def test_currency_is_immutable() -> None:
    usd = Currency("USD")
    with pytest.raises(AttributeError):
        usd.code = "EUR"
    with pytest.raises(AttributeError):
        del usd.minor


def test_subclass_cache_is_isolated_both_ways() -> None:
    crypto = Crypto("EUR")
    base = Currency("EUR")
    assert crypto is not base
    assert type(base) is Currency
    assert Crypto("eur") is crypto


def test_subclass_pickles_as_itself() -> None:
    c = Crypto("KWD")
    assert pickle.loads(pickle.dumps(c)) is c


def test_copy_returns_same_object() -> None:
    usd = Currency("USD")
    assert copy.copy(usd) is usd
    assert copy.deepcopy(usd) is usd


def test_creation_race_with_sleep_in_critical_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Racy(Currency):
        pass

    original = Racy._create.__func__  # type: ignore[attr-defined]

    def slow_create(cls: type[Racy], key: str) -> Racy:
        time.sleep(0.05)  # widen the window inside the locked section
        return original(cls, key)  # type: ignore[no-any-return]

    monkeypatch.setattr(Racy, "_create", classmethod(slow_create))

    barrier = threading.Barrier(4)
    results: list[Currency] = []

    def worker() -> None:
        barrier.wait()
        results.append(Racy("IRR"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    assert len({id(r) for r in results}) == 1


# --- Money ------------------------------------------------------------------


def test_money_has_no_init_and_exact_slots() -> None:
    assert "__init__" not in Money.__dict__
    assert Money.__slots__ == ("amount", "currency")


@pytest.mark.parametrize("amount", [1, "1", "1.0", Decimal("1.00")])
def test_accepted_amount_types(amount: int | str | Decimal) -> None:
    assert Money(amount, "USD").amount == Decimal("1.00")


def test_float_is_type_error() -> None:
    with pytest.raises(TypeError):
        Money(1.5, "USD")  # type: ignore[arg-type]


def test_bool_is_type_error() -> None:
    with pytest.raises(TypeError):
        Money(True, "USD")


@pytest.mark.parametrize(
    ("amount", "code"), [("19.999", "USD"), ("1.5", "JPY"), ("0.0001", "KWD")]
)
def test_strict_constructor_rejects_extra_places(amount: str, code: str) -> None:
    with pytest.raises(ValueError):
        Money(amount, code)


@pytest.mark.parametrize("amount", ["abc", "NaN", "Infinity"])
def test_invalid_amounts_are_value_errors(amount: str) -> None:
    with pytest.raises(ValueError):
        Money(amount, "USD")


def test_rounded_half_even_and_custom_rounding() -> None:
    assert Money.rounded("0.125", "USD").amount == Decimal("0.12")
    assert Money.rounded("0.135", "USD").amount == Decimal("0.14")
    assert Money.rounded("0.125", "USD", ROUND_HALF_UP).amount == Decimal("0.13")
    assert type(USDMoney.rounded("1.005", "USD")) is USDMoney


def test_add_sub() -> None:
    assert Money("5.00", "EUR") - Money("0.01", "EUR") == Money("4.99", "EUR")


def test_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatch):
        Money(1, "USD") + Money(1, "EUR")
    with pytest.raises(ValueError):
        Money(1, "USD") - Money(1, "EUR")
    with pytest.raises(CurrencyMismatch):
        Money(1, "USD") + Money(1, Crypto("USD"))


def test_non_money_operand_returns_not_implemented() -> None:
    m = Money(1, "USD")
    assert m.__add__(1) is NotImplemented
    assert m.__sub__(Decimal(1)) is NotImplemented
    with pytest.raises(TypeError):
        m + 1


def test_multiplication() -> None:
    m = Money("0.05", "USD")
    assert m * 3 == Money("0.15", "USD")
    assert 3 * m == Money("0.15", "USD")
    assert m * Decimal("0.5") == Money("0.02", "USD")  # 0.025 -> half-even 0.02
    assert Decimal("0.5") * m == Money("0.02", "USD")
    assert m.__mul__(m) is NotImplemented
    assert m.__mul__(1.5) is NotImplemented
    with pytest.raises(TypeError):
        m * m


def test_neg_abs_and_subclass_preservation() -> None:
    m = USDMoney("2.50", "USD")
    assert -m == Money("-2.50", "USD")
    assert abs(-m) == m
    for result in (m + m, m - m, m * 2, 2 * m, -m, abs(m)):
        assert type(result) is USDMoney


def test_negative_zero_is_normalised() -> None:
    assert repr(-Money(0, "USD")) == "Money('0.00', 'USD')"


def test_equality_and_hash() -> None:
    assert Money(1, "USD") == Money("1.00", "USD")
    assert Money(1, "USD") != Decimal(1)
    assert Money(1, "USD") != Money(1, "EUR")
    assert len({Money(1, "USD"), Money("1.0", "USD"), USDMoney(1, "USD")}) == 1


def test_ordering() -> None:
    a, b = Money(1, "USD"), Money(2, "USD")
    assert a < b and a <= b and a <= Money("1.00", "USD")
    assert b > a and b >= a
    with pytest.raises(CurrencyMismatch):
        _ = a < Money(2, "EUR")
    with pytest.raises(TypeError):
        _ = a < 2


def test_repr() -> None:
    assert repr(Money("19.99", "USD")) == "Money('19.99', 'USD')"
    assert repr(Money(1000, "JPY")) == "Money('1000', 'JPY')"
    assert repr(USDMoney(1, "USD")) == "USDMoney('1.00', 'USD')"


def test_money_is_immutable() -> None:
    m = Money(1, "USD")
    with pytest.raises(AttributeError):
        m.amount = Decimal(2)


@pytest.mark.parametrize(
    "money", [Money("19.99", "USD"), USDMoney(3, "USD"), Money(5, Crypto("JPY"))]
)
def test_money_pickle_round_trip(money: Money) -> None:
    clone = pickle.loads(pickle.dumps(money))
    assert clone == money
    assert type(clone) is type(money)
    assert clone.currency is money.currency
