from __future__ import annotations

import threading
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from typing import Any, ClassVar, Self, cast
from weakref import WeakKeyDictionary, WeakValueDictionary

__all__ = [
    "ISO_4217",
    "MINOR_UNITS",
    "CardPayment",
    "CashPayment",
    "Currency",
    "CurrencyMismatch",
    "Money",
    "PaymentMethod",
    "WalletPayment",
]

# code -> (ISO 4217 name, minor units)
ISO_4217: dict[str, tuple[str, int]] = {
    "USD": ("US Dollar", 2),
    "EUR": ("Euro", 2),
    "JPY": ("Yen", 0),
    "KWD": ("Kuwaiti Dinar", 3),
    "BHD": ("Bahraini Dinar", 3),
    "NOK": ("Norwegian Krone", 2),
    "IRR": ("Iranian Rial", 0),
}

MINOR_UNITS: dict[str, int] = {code: minor for code, (_, minor) in ISO_4217.items()}

# M5:
# Every Decimal operation below names this context explicitly. Without it,
# money arithmetic silently inherits whatever the *caller's* thread happens to
# have installed: a caller who has set `getcontext().prec = 3` would make
# Money("100.00", "USD") raise "out of range", and one who has set
# ROUND_UP would move rounded() off half-even. The traps are the decimal
# defaults, so trapping behaviour is unchanged -- only the ambient dependency
# goes away.
MONEY_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

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
            _, minor = ISO_4217[key]
        except KeyError:
            raise ValueError(f"{key!r} is not a known ISO 4217 currency code") from None
        currency = object.__new__(cls)
        object.__setattr__(currency, "code", key)
        object.__setattr__(currency, "minor", minor)
        return currency

    # M6:
    # name and minor_units are properties, not slots, so the slot layout stays
    # exactly ("code", "minor", "__weakref__") and instances keep no __dict__.
    # minor_units is the spelled-out alias for the stored `minor`.
    @property
    def name(self) -> str:
        return ISO_4217[self.code][0]

    @property
    def minor_units(self) -> int:
        return self.minor

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
    return Decimal(1).scaleb(-currency.minor, MONEY_CONTEXT)


class Money:
    __slots__ = ("amount", "currency")

    amount: Decimal
    currency: Currency

    def __new__(cls, amount: int | str | Decimal, currency: Currency | str) -> Self:
        cur = _as_currency(currency)
        value = _as_decimal(amount)
        try:
            exact = value.quantize(_quantum(cur), context=MONEY_CONTEXT)
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
            exact = value.quantize(
                _quantum(cur), rounding=rounding, context=MONEY_CONTEXT
            )
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
        return type(self)(MONEY_CONTEXT.add(self.amount, other.amount), self.currency)

    def __sub__(self, other: object) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return type(self)(
            MONEY_CONTEXT.subtract(self.amount, other.amount), self.currency
        )

    def __mul__(self, factor: object) -> Self:
        if isinstance(factor, bool) or not isinstance(factor, int | Decimal):
            return NotImplemented
        return type(self).rounded(
            MONEY_CONTEXT.multiply(self.amount, factor), self.currency
        )

    __rmul__ = __mul__

    def __neg__(self) -> Self:
        return type(self)(MONEY_CONTEXT.minus(self.amount), self.currency)

    def __abs__(self) -> Self:
        return type(self)(MONEY_CONTEXT.abs(self.amount), self.currency)

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


# ---------------------------------------------------------------------------
# Payment methods: a factory base that dispatches on a registered kind
# ---------------------------------------------------------------------------


class _PaymentMethodMeta(type):
    """
    Metaclass that finishes what ``PaymentMethod.__new__`` starts.

    P4:
    Dispatch has to live in ``__new__`` -- that is where the class is still a
    free choice. But ``type.__call__`` then calls
    ``type(instance).__init__(instance, *args, **kw)`` with the *factory's*
    arguments, so ``PaymentMethod("card", last4="4242")`` would hand the kind
    selector to ``CardPayment.__init__`` as its ``last4`` positional and blow
    up with "got multiple values for argument 'last4'".

    Overriding ``__call__`` is the only hook between ``__new__`` choosing the
    class and ``__init__`` being handed the arguments. When ``__new__``
    returned a class other than the one called, the first positional was the
    kind selector and is dropped; a direct ``CardPayment(...)`` is untouched.
    """

    def __call__(cls, *args: Any, **kw: Any) -> Any:
        # cast: mypy types `cls.__new__` off the metaclass rather than off the
        # class being constructed, so it cannot see the real signature here.
        instance: Any = cast(Any, cls).__new__(cls, *args, **kw)
        if not isinstance(instance, cls):
            return instance
        # A dispatched construction: __new__ chose a different class, so
        # args[0] was the kind and belongs to the factory, not to __init__.
        init_args = args[1:] if type(instance) is not cls else args
        type(instance).__init__(instance, *init_args, **kw)
        return instance


class PaymentMethod(metaclass=_PaymentMethodMeta):
    """
    Base and factory. ``PaymentMethod(kind, **kw)`` returns an instance of the
    subclass registered for ``kind``; subclasses stay directly constructible.
    """

    __slots__ = ()

    kind: ClassVar[str] = ""
    _registry: ClassVar[dict[str, type[PaymentMethod]]] = {}

    def __init_subclass__(cls, *, kind: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # A subclass without a kind is a perfectly good class; it is simply
        # not reachable through the factory.
        if kind is None:
            return
        if not isinstance(kind, str) or not kind.strip():
            raise TypeError(f"{cls.__qualname__}: kind must be a non-empty str")
        key = kind.strip().lower()
        existing = PaymentMethod._registry.get(key)
        if existing is not None:
            raise ValueError(
                f"payment kind {key!r} is already registered to "
                f"{existing.__qualname__}; cannot register {cls.__qualname__}"
            )
        cls.kind = key
        PaymentMethod._registry[key] = cls

    def __new__(cls, *args: Any, **kw: Any) -> PaymentMethod:
        # Direct construction of a concrete subclass: nothing to dispatch.
        if cls is not PaymentMethod:
            return super().__new__(cls)
        if not args:
            raise TypeError(
                "PaymentMethod() missing 1 required positional argument: 'kind'"
            )
        kind = args[0]
        if not isinstance(kind, str):
            raise TypeError(f"kind must be a str, not {type(kind).__name__}")
        key = kind.strip().lower()
        target = PaymentMethod._registry.get(key)
        if target is None:
            known = ", ".join(sorted(PaymentMethod._registry)) or "<none>"
            raise ValueError(f"unknown payment kind {kind!r}; known kinds: {known}")
        return super().__new__(target)

    def __repr__(self) -> str:
        fields = ", ".join(
            f"{name}={getattr(self, name)!r}" for name in self._repr_fields()
        )
        return f"{type(self).__name__}({fields})"

    @classmethod
    def _repr_fields(cls) -> tuple[str, ...]:
        """Slot names across the MRO, base-most first, for repr and equality."""
        names: list[str] = []
        for entry in reversed(cls.__mro__):
            for name in getattr(entry, "__slots__", ()):
                if name not in names:
                    names.append(name)
        return tuple(names)


class CardPayment(PaymentMethod, kind="card"):
    __slots__ = ("last4", "holder")

    last4: str
    holder: str

    def __init__(self, last4: str, holder: str = "") -> None:
        if not isinstance(last4, str) or not (last4.isdigit() and len(last4) == 4):
            raise ValueError(f"last4 must be exactly 4 digits, got {last4!r}")
        self.last4 = last4
        self.holder = holder.strip()


class CashPayment(PaymentMethod, kind="cash"):
    __slots__ = ("tendered_cents",)

    tendered_cents: int

    def __init__(self, tendered_cents: int) -> None:
        if isinstance(tendered_cents, bool) or not isinstance(tendered_cents, int):
            raise TypeError(
                f"tendered_cents must be an int, not {type(tendered_cents).__name__}"
            )
        if tendered_cents < 0:
            raise ValueError(
                f"tendered_cents cannot be negative, got {tendered_cents!r}"
            )
        self.tendered_cents = tendered_cents


class WalletPayment(PaymentMethod, kind="wallet"):
    __slots__ = ("provider", "wallet_id", "token")

    provider: str
    wallet_id: str
    token: str

    def __init__(self, provider: str, wallet_id: str, token: str = "") -> None:
        if not provider.strip():
            raise ValueError("provider must be a non-empty str")
        if not wallet_id.strip():
            raise ValueError("wallet_id must be a non-empty str")
        self.provider = provider.strip().lower()
        self.wallet_id = wallet_id.strip()
        self.token = token
