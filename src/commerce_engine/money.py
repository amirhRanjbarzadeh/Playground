from __future__ import annotations

import threading
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Self
from weakref import WeakKeyDictionary, WeakValueDictionary

__all__ = ["MINOR_UNITS", "Currency", "CurrencyMismatch", "Money"]

MINOR_UNITS: dict[str, int] = {"USD": 2, "EUR": 2, "JPY": 0, "KWD": 3, "IRR": 0}

_CACHES: WeakKeyDictionary[type[Currency], WeakValueDictionary[str, Currency]] = (
    WeakKeyDictionary()
)
_LOCK = threading.Lock()


class Currency:
    __slots__ = ("code", "minor", "__weakref__")

    code: str
    minor: int

    def __new__(cls, code: str) -> Self:
        if not isinstance(code, str):
            raise TypeError(f"currency code must be a str, not {type(code).__name__}")
        key = code.strip().upper()

        # Fast path: no lock once the instance exists.
        cache = _CACHES.get(cls)
        if cache is not None and (found := cache.get(key)) is not None:
            return found  # type: ignore[return-value]

        with _LOCK:
            cache = _CACHES.setdefault(cls, WeakValueDictionary())
            found = cache.get(key)
            if found is None:
                found = cls._create(key)
                cache[key] = found
            return found  # type: ignore[return-value]

    @classmethod
    def _create(cls, key: str) -> Self:
        """Build a fresh instance. Called only while holding the creation lock."""
        try:
            minor = MINOR_UNITS[key]
        except KeyError:
            raise ValueError(f"unknown currency code {key!r}") from None
        currency = object.__new__(cls)
        object.__setattr__(currency, "code", key)
        object.__setattr__(currency, "minor", minor)
        return currency

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; cannot delete {name!r}"
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.code!r})"

    def __str__(self) -> str:
        return self.code

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self

    def __reduce__(self) -> tuple[type[Self], tuple[str]]:
        # type(self), not Currency: a Crypto must unpickle as a Crypto (the D2 bug).
        return (type(self), (self.code,))


class CurrencyMismatch(ValueError):
    """Raised when an operation mixes Money of different currencies."""


def _as_currency(currency: Currency | str) -> Currency:
    if isinstance(currency, Currency):
        return currency
    if isinstance(currency, str):
        return Currency(currency)
    raise TypeError(
        f"currency must be a Currency or str, not {type(currency).__name__}"
    )


def _as_decimal(amount: int | str | Decimal) -> Decimal:
    if isinstance(amount, float):
        raise TypeError("float amounts are not accepted; pass a str or Decimal")
    if isinstance(amount, bool) or not isinstance(amount, int | str | Decimal):
        raise TypeError(
            f"amount must be int, str or Decimal, not {type(amount).__name__}"
        )
    try:
        value = Decimal(str(amount))
    except InvalidOperation:
        raise ValueError(f"invalid amount {amount!r}") from None
    if not value.is_finite():
        raise ValueError(f"amount must be finite, got {amount!r}")
    return value


def _quantum(currency: Currency) -> Decimal:
    return Decimal(1).scaleb(-currency.minor)


class Money:
    __slots__ = ("amount", "currency")

    amount: Decimal
    currency: Currency

    def __new__(cls, amount: int | str | Decimal, currency: Currency | str) -> Self:
        cur = _as_currency(currency)
        value = _as_decimal(amount)
        try:
            exact = value.quantize(_quantum(cur))
        except InvalidOperation:
            raise ValueError(f"amount {amount!r} is out of range") from None
        if exact != value:
            raise ValueError(
                f"{amount!r} has more than {cur.minor} decimal places for {cur.code}; "
                f"use {cls.__name__}.rounded() to round explicitly"
            )
        if not exact:
            exact = exact.copy_abs()  # no "-0.00"
        money = object.__new__(cls)
        object.__setattr__(money, "amount", exact)
        object.__setattr__(money, "currency", cur)
        return money

    @classmethod
    def rounded(
        cls,
        amount: int | str | Decimal,
        currency: Currency | str,
        rounding: str = ROUND_HALF_EVEN,
    ) -> Self:
        cur = _as_currency(currency)
        value = _as_decimal(amount)
        try:
            exact = value.quantize(_quantum(cur), rounding=rounding)
        except InvalidOperation:
            raise ValueError(f"amount {amount!r} is out of range") from None
        return cls(exact, cur)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; cannot delete {name!r}"
        )

    def _same_currency(self, other: Money) -> None:
        if self.currency is not other.currency:
            raise CurrencyMismatch(
                f"cannot combine {self.currency.code} with {other.currency.code}"
            )

    def __add__(self, other: object) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return type(self)(self.amount + other.amount, self.currency)

    def __sub__(self, other: object) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return type(self)(self.amount - other.amount, self.currency)

    def __mul__(self, factor: object) -> Self:
        if isinstance(factor, bool) or not isinstance(factor, int | Decimal):
            return NotImplemented
        return type(self).rounded(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> Self:
        return type(self)(-self.amount, self.currency)

    def __abs__(self) -> Self:
        return type(self)(abs(self.amount), self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.currency is other.currency and self.amount == other.amount

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))

    # > and >= come for free: Python reflects them onto __lt__ / __le__.
    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount <= other.amount

    def __repr__(self) -> str:
        return f"{type(self).__name__}({f'{self.amount:f}'!r}, {self.currency.code!r})"

    def __reduce__(self) -> tuple[type[Self], tuple[Decimal, Currency]]:
        return (type(self), (self.amount, self.currency))
