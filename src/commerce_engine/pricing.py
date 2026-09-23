"""
A cooperative pricing pipeline.

Every rule is a mixin over :class:`PriceRule`. ``adjust`` walks *down* the MRO
via ``super()`` until it hits the terminator, then each rule transforms the
result on the way back *up*. So the **rightmost** rule in the bases list
transforms first and the **leftmost** transforms last.

Rounding is deliberately *not* a rule -- see R3 in
``docs/adr/0001-rounding-is-not-a-rule.md``. :meth:`PriceRule.price` quantizes
to the currency's minor units as its own final step, after ``adjust`` has
returned, so no ordering constraint on the MRO can be got wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from typing import Any, ClassVar

from commerce_engine.c3 import linearize
from commerce_engine.money import Currency, CurrencyMismatch, Money

__all__ = [
    "DiscountCap",
    "DiscountThenTax",
    "FixedDiscount",
    "PercentTax",
    "PriceContext",
    "PriceRule",
    "Running",
    "TaxThenDiscount",
    "UnknownPriceConfig",
]


# ---------------------------------------------------------------------------
# Explicit arithmetic context
# ---------------------------------------------------------------------------

# P1:
# Saturday's finding was that money arithmetic silently inherited whatever
# decimal context the *caller's* thread happened to have installed -- a caller
# doing `getcontext().prec = 4` would quietly change prices. Every arithmetic
# operation below names this context explicitly, so the pipeline's results
# depend only on its inputs. The traps turn silent nonsense (a NaN from an
# overflowing multiply) into a loud exception.
PRICE_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)

_ZERO = Decimal(0)
_ONE = Decimal(1)


def _quantum(currency: Currency) -> Decimal:
    # scaleb reads the context too, so it gets the explicit one as well.
    return _ONE.scaleb(-currency.minor, PRICE_CONTEXT)


def _quantize(amount: Decimal, currency: Currency) -> Decimal:
    return amount.quantize(
        _quantum(currency), rounding=ROUND_HALF_EVEN, context=PRICE_CONTEXT
    )


def _as_rate(value: object, *, name: str) -> Decimal:
    """Accept a Decimal (or int) rate; reject float, as money.py does."""
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must be a Decimal, not {type(value).__name__}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} must be finite, got {value!r}")
        return value
    raise TypeError(f"{name} must be a Decimal, not {type(value).__name__}")


# ---------------------------------------------------------------------------
# What flows through the pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Running:
    """
    The two amounts a rule transforms, carried together.

    ``amount`` is the running price. ``undiscounted`` is the same running
    price as it would stand if every discount rule were a no-op; DiscountCap
    measures against it so that the cap means the same thing wherever it sits
    in the pipeline (see P6).

    R1/R2:
    These two used to be split -- the amount was the argument and the shadow
    was a mutable box hidden inside the "frozen" PriceContext. Two things
    followed from that. A context was single-use: each ``adjust`` rescaled the
    shadow in place, so a second call with the same context started from the
    first call's shadow and silently returned a different price. And the
    shadow's invariant held only by convention, because updating it was a side
    effect a new rule could simply forget. Returning both amounts makes a
    forgotten shadow a visible omission in the rule's ``return``, and leaves
    nothing for an evaluation to mutate.
    """

    amount: Decimal
    undiscounted: Decimal


@dataclass(frozen=True, slots=True)
class PriceContext:
    """
    What the pipeline knows besides the running amounts: the amount handed to
    the outermost ``adjust`` call, and the currency everything is denominated
    in.

    Genuinely immutable, and therefore reusable and safe to share between
    threads.
    """

    base: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if not isinstance(self.base, Decimal):
            raise TypeError(f"base must be a Decimal, not {type(self.base).__name__}")
        if not isinstance(self.currency, Currency):
            raise TypeError(
                f"currency must be a Currency, not {type(self.currency).__name__}"
            )

    def start(self) -> Running:
        """The Running an evaluation begins from: nothing discounted yet."""
        return Running(amount=self.base, undiscounted=self.base)

    @classmethod
    def for_amount(cls, amount: Money) -> PriceContext:
        return cls(base=amount.amount, currency=amount.currency)


class UnknownPriceConfig(TypeError):
    """Raised when a rule is constructed with a config key nothing consumes."""


# ---------------------------------------------------------------------------
# Root / terminator
# ---------------------------------------------------------------------------


class PriceRule:
    """
    Root of the cooperative chain, and the terminator for both ``__init__``
    and ``adjust``.
    """

    # Keys this class's __init__ consumes. Every mixin declares its own; the
    # union over the MRO is the set of keys a composed class accepts.
    _config_keys: ClassVar[frozenset[str]] = frozenset()

    # R4:
    # That union, resolved once. Computing it per construction meant running
    # C3 on every instance, which was about 80% of the cost of building a
    # rule (64 us against 2.4 us for a plain __init__ with the same kwargs).
    # Class creation is the one moment where the MRO is both known and fixed,
    # and __init_subclass__ already walks it to validate the composition, so
    # the answer is computed there and stored as a plain frozenset. Nothing at
    # construction time touches the linearizer.
    _accepted_config_keys: ClassVar[frozenset[str]] = frozenset()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        mro = linearize(cls)
        _validate_composition(cls, mro)
        cls._accepted_config_keys = _accepted_keys(mro)

    # P2:
    # Why a domain error, and why it is raised here in __new__ rather than
    # left to object.__init__.
    #
    # object.__init__'s own complaint is useless for this:
    #
    #     TypeError: object.__init__() takes exactly one argument
    #                (the instance to initialize)
    #
    # It names neither the offending key nor the class, and it talks about
    # *positional* arguments while the caller passed a keyword.
    #
    # Worse, for the case that actually matters it never runs at all. A typo
    # is a swap, not an addition: a misspelled tax_rate= both adds an unknown
    # key *and* removes the real one, so the cooperative chain dies at
    #
    #     TypeError: PercentTax.__init__() missing 1 required keyword-only
    #                argument: 'tax_rate'
    #
    # long before any leftover reaches the terminator. That message points at
    # the wrong thing -- it accuses the caller of forgetting a key they
    # believe they passed, and never mentions the typo itself.
    #
    # So the check moves ahead of the whole chain, into __new__, where the
    # full kwargs are still intact and nothing has had a chance to fail
    # first. The union of _config_keys over the MRO is the set of keys this
    # particular composition accepts, so the error can name both the typo and
    # the alternatives.
    def __new__(cls, **kw: Any) -> PriceRule:
        accepted = cls._accepted_config_keys
        unknown = set(kw) - accepted
        if unknown:
            offending = ", ".join(repr(key) for key in sorted(unknown))
            known = ", ".join(repr(key) for key in sorted(accepted))
            raise UnknownPriceConfig(
                f"{cls.__qualname__}: unknown config key(s) {offending}; "
                f"accepted keys: {known or '<none>'}"
            )
        return super().__new__(cls)

    def __init__(self, **kw: Any) -> None:
        # Backstop: __new__ has already rejected keys no rule declares, so
        # anything still here was declared by some rule and then not consumed
        # -- a bug in that rule's forwarding, not in the caller's config.
        if kw:
            leftover = ", ".join(repr(key) for key in sorted(kw))
            raise UnknownPriceConfig(
                f"{type(self).__qualname__}: config key(s) {leftover} were "
                f"declared but never consumed; a rule is not forwarding **kw"
            )
        super().__init__()

    def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
        """Terminator: the unadjusted amounts. Deliberately does not call super()."""
        return running

    def price(self, amount: Money) -> Money:
        """
        Run the whole pipeline over a Money and return a Money.

        R3:
        Quantizing here, rather than in a Rounding mixin, is what makes
        "rounding runs last" a fact about the code instead of a convention the
        MRO has to be validated against. ``adjust`` works at full precision
        throughout; the single rounding step happens once, after it returns.
        """
        ctx = PriceContext.for_amount(amount)
        final = self.adjust(ctx.start(), ctx=ctx)
        return Money(_quantize(final.amount, ctx.currency), ctx.currency)


# ---------------------------------------------------------------------------
# Composition validation
# ---------------------------------------------------------------------------


def _validate_composition(cls: type, mro: tuple[type, ...]) -> None:
    # B4:
    # PriceRule.adjust is the terminator and deliberately does not call
    # super(), so anything the MRO places *after* the root can never run. A
    # rule mixin stacked behind PriceRule is silently dead code, and a silent
    # no-op on a pricing pipeline is a money bug waiting to happen.
    #
    # This is the only structural rule left. The ordering check that used to
    # live here -- "Rounding must be leftmost" -- is gone along with Rounding
    # itself. C3 can see structure, never what an adjust override does, so
    # that check could neither be made sound (a Rounding subclass could
    # rescale after rounding and still pass) nor kept free of false positives
    # (a two-level delegating specialization was rejected). See R3.
    if mro[-2] is not PriceRule:
        root = mro.index(PriceRule)
        trailing = " -> ".join(entry.__name__ for entry in mro[root:])
        raise TypeError(
            f"{cls.__qualname__}: PriceRule must be last in the MRO because it "
            f"terminates the chain, but it is followed by "
            f"{mro[root + 1].__name__}; tail is {trailing}"
        )


def _accepted_keys(mro: tuple[type, ...]) -> frozenset[str]:
    keys: frozenset[str] = frozenset()
    for entry in mro:
        declared = vars(entry).get("_config_keys")
        if declared is not None:
            keys |= declared
    return keys


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class FixedDiscount(PriceRule):
    """Subtract a fixed Money amount; never take the price below zero."""

    _config_keys: ClassVar[frozenset[str]] = frozenset({"discount"})

    def __init__(self, *, discount: Money, **kw: Any) -> None:
        if not isinstance(discount, Money):
            raise TypeError(f"discount must be a Money, not {type(discount).__name__}")
        if discount.amount < _ZERO:
            raise ValueError(f"discount must not be negative, got {discount!r}")
        self.discount = discount
        super().__init__(**kw)

    def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
        result = super().adjust(running, ctx=ctx)
        if self.discount.currency is not ctx.currency:
            raise CurrencyMismatch(
                f"discount is {self.discount.currency.code} but the price is "
                f"{ctx.currency.code}"
            )
        discounted = PRICE_CONTEXT.subtract(result.amount, self.discount.amount)
        return Running(
            amount=discounted if discounted > _ZERO else _ZERO,
            # Carried through unchanged: that is what makes it the
            # "no discounts applied" amount.
            undiscounted=result.undiscounted,
        )


class PercentTax(PriceRule):
    """Multiply by (1 + tax_rate)."""

    _config_keys: ClassVar[frozenset[str]] = frozenset({"tax_rate"})

    def __init__(self, *, tax_rate: Decimal, **kw: Any) -> None:
        rate = _as_rate(tax_rate, name="tax_rate")
        if rate < _ZERO:
            raise ValueError(f"tax_rate must not be negative, got {tax_rate!r}")
        self.tax_rate = rate
        super().__init__(**kw)

    def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
        result = super().adjust(running, ctx=ctx)
        factor = PRICE_CONTEXT.add(_ONE, self.tax_rate)
        # R2:
        # Both components scale, so a later DiscountCap compares like with
        # like. Scaling only one would be a visible asymmetry in this return,
        # which is the whole point of returning them together.
        return Running(
            amount=PRICE_CONTEXT.multiply(result.amount, factor),
            undiscounted=PRICE_CONTEXT.multiply(result.undiscounted, factor),
        )


class DiscountCap(PriceRule):
    """
    Clamp the discount applied so far to at most ``max_discount_pct`` of the
    amount as it would stand with no discounts.

    P6:
    "The discount applied so far" used to be computed as ``ctx.base - amount``
    and the clamp used to return ``ctx.base - cap``. Both are only correct
    while nothing else has touched the amount. Once PercentTax has run,
    ``ctx.base - amount`` conflates the discount with the tax, and returning
    ``ctx.base - cap`` throws the tax away outright:

        base 100, discount 20, tax 9%, cap 5%
        -> (100 - 20) * 1.09 = 87.20, then the clamp returned 100 - 5 = 95.00

    95.00 carries no tax at all -- the rule silently un-taxed the order (B1).

    The fix is to compare against ``Running.undiscounted``, which every
    scaling rule carries forward. The cap then means the same thing wherever
    it sits, and a clamp preserves whatever tax has been applied:

        -> cap = 5% of 109.00 = 5.45, so the result is 109.00 - 5.45 = 103.55
    """

    _config_keys: ClassVar[frozenset[str]] = frozenset({"max_discount_pct"})

    def __init__(self, *, max_discount_pct: Decimal, **kw: Any) -> None:
        pct = _as_rate(max_discount_pct, name="max_discount_pct")
        if not (_ZERO <= pct <= _ONE):
            raise ValueError(
                f"max_discount_pct must be between 0 and 1, got {max_discount_pct!r}"
            )
        self.max_discount_pct = pct
        super().__init__(**kw)

    def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
        result = super().adjust(running, ctx=ctx)
        reference = result.undiscounted
        cap = PRICE_CONTEXT.multiply(reference, self.max_discount_pct)
        applied = PRICE_CONTEXT.subtract(reference, result.amount)
        if applied <= cap:
            return result
        return Running(
            amount=PRICE_CONTEXT.subtract(reference, cap),
            undiscounted=reference,
        )


# ---------------------------------------------------------------------------
# Two checkouts that differ only in base order
# ---------------------------------------------------------------------------
#
# Same three rules, same config, different order -- and the order moves the
# money, because subtracting a fixed amount and multiplying by a rate do not
# commute.
#
# DiscountThenTax.__mro__:
#   DiscountThenTax, PercentTax, DiscountCap, FixedDiscount, PriceRule, object
#
# TaxThenDiscount.__mro__:
#   TaxThenDiscount, DiscountCap, FixedDiscount, PercentTax, PriceRule, object
#
# Transforms run right-to-left along the MRO (super() first, transform after),
# so for amount=100.00, discount=10, tax_rate=0.09, max_discount_pct=0.20,
# with price() quantizing once at the end:
#
#   DiscountThenTax  FixedDiscount  100.00 - 10 = 90.00   (shadow 100.00)
#                    DiscountCap    cap = 20% of 100.00 = 20.00; applied
#                                   = 10.00 <= 20.00, so no clamp
#                    PercentTax     90.00 * 1.09 = 98.1000 (shadow 109.00)
#                    price()        USD -> 98.10      IRR -> 98
#
#   TaxThenDiscount  PercentTax     100.00 * 1.09 = 109.00 (shadow 109.00)
#                    FixedDiscount  109.00 - 10 = 99.00
#                    DiscountCap    cap = 20% of 109.00 = 21.80; applied
#                                   = 10.00 <= 21.80, so no clamp
#                    price()        USD -> 99.00      IRR -> 99
#
# USD: 98.10 vs 99.00. IRR (zero minor units): 98 vs 99.
# Taxing before the discount is worth 0.90 to the merchant on a 100.00 order,
# because the 10.00 comes off a figure that has already been grossed up.
#
# The cap is inert at these numbers by design -- a cap that binds normalizes
# both orders to the same answer, since a proportional tax commutes with a
# proportional cap. Raise the discount to 30 and it binds: DiscountThenTax
# gives (100 - 20) * 1.09 = 87.20 and TaxThenDiscount gives 109 - 21.80 =
# 87.20. Equal, and crucially still taxed -- that equality is the B1 fix.


class DiscountThenTax(PercentTax, DiscountCap, FixedDiscount, PriceRule):
    """Discount the order, cap the discount, then tax what is left."""


class TaxThenDiscount(DiscountCap, FixedDiscount, PercentTax, PriceRule):
    """Tax the order first, then take the discount off the taxed figure."""
