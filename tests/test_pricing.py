from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_UP, Decimal, localcontext
from typing import Any

import pytest

from commerce_engine import pricing
from commerce_engine.money import Currency, CurrencyMismatch, Money
from commerce_engine.pricing import (
    DiscountCap,
    DiscountThenTax,
    FixedDiscount,
    PercentTax,
    PriceContext,
    PriceRule,
    Running,
    TaxThenDiscount,
    UnknownPriceConfig,
)

TAX = Decimal("0.09")
CAP = Decimal("0.20")


def _config(code: str, discount: str | None = None) -> dict[str, object]:
    return {
        "tax_rate": TAX,
        "max_discount_pct": CAP,
        "discount": Money(discount or ("10.00" if code == "USD" else "10"), code),
    }


def _ctx(code: str, base: str) -> PriceContext:
    return PriceContext(base=Decimal(base), currency=Currency(code))


def _run(amount: str, undiscounted: str | None = None) -> Running:
    return Running(
        amount=Decimal(amount),
        undiscounted=Decimal(undiscounted if undiscounted is not None else amount),
    )


class Scaling(PriceRule):
    """Test-only rule that multiplies both components by a factor."""

    _config_keys = frozenset({"factor"})

    def __init__(self, *, factor: Decimal, **kw: Any) -> None:
        self.factor = factor
        super().__init__(**kw)

    def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
        result = super().adjust(running, ctx=ctx)
        return Running(
            amount=result.amount * self.factor,
            undiscounted=result.undiscounted * self.factor,
        )


# ---------------------------------------------------------------------------
# The two pinned checkouts
# ---------------------------------------------------------------------------


def test_pinned_mros() -> None:
    assert [c.__name__ for c in DiscountThenTax.__mro__] == [
        "DiscountThenTax",
        "PercentTax",
        "DiscountCap",
        "FixedDiscount",
        "PriceRule",
        "object",
    ]
    assert [c.__name__ for c in TaxThenDiscount.__mro__] == [
        "TaxThenDiscount",
        "DiscountCap",
        "FixedDiscount",
        "PercentTax",
        "PriceRule",
        "object",
    ]


@pytest.mark.parametrize(
    ("cls", "code", "amount", "expected"),
    [
        (DiscountThenTax, "USD", "100.00", "98.10"),
        (TaxThenDiscount, "USD", "100.00", "99.00"),
        (DiscountThenTax, "IRR", "100", "98"),
        (TaxThenDiscount, "IRR", "100", "99"),
    ],
)
def test_base_order_changes_the_money(
    cls: type[PriceRule], code: str, amount: str, expected: str
) -> None:
    rule = cls(**_config(code))
    assert rule.price(Money(amount, code)) == Money(expected, code)


# ---------------------------------------------------------------------------
# B1: a binding cap must not strip tax
# ---------------------------------------------------------------------------


def test_b1_binding_cap_keeps_the_tax() -> None:
    """
    The old clamp returned ``ctx.base - cap``, a pre-tax figure, so a cap that
    bound after PercentTax silently un-taxed the order.
    """
    rule = TaxThenDiscount(**_config("USD", discount="30.00"))  # type: ignore[arg-type]
    result = rule.price(Money("100.00", "USD"))

    # 109.00 taxed, capped at 20% of 109.00 = 21.80 -> 87.20.
    assert result == Money("87.20", "USD")
    # The pre-fix answer, with the tax thrown away:
    assert result != Money("80.00", "USD")


def test_b1_cap_after_tax_in_the_original_reported_order() -> None:
    """The exact shape from the report: DiscountCap after PercentTax."""

    class CapAfterTax(DiscountCap, PercentTax, FixedDiscount, PriceRule):
        pass

    rule = CapAfterTax(
        tax_rate=TAX,
        max_discount_pct=Decimal("0.05"),
        discount=Money("20.00", "USD"),
    )
    result = rule.price(Money("100.00", "USD"))

    # cap = 5% of the taxed 109.00 = 5.45 -> 103.55, still taxed.
    assert result == Money("103.55", "USD")
    assert result != Money("95.00", "USD")  # the reported wrong answer


def test_b1_a_binding_cap_makes_both_orders_agree() -> None:
    """
    A proportional cap commutes with a proportional tax, so once the cap binds
    both checkouts must land on the same money. Before the fix they did not.
    """
    discount = "30.00"
    a = DiscountThenTax(**_config("USD", discount=discount))  # type: ignore[arg-type]
    b = TaxThenDiscount(**_config("USD", discount=discount))  # type: ignore[arg-type]
    assert a.price(Money("100.00", "USD")) == b.price(Money("100.00", "USD"))


def test_b1_cap_is_inert_when_the_discount_is_under_it() -> None:
    rule = DiscountThenTax(**_config("USD"))  # type: ignore[arg-type]
    assert rule.price(Money("100.00", "USD")) == Money("98.10", "USD")


# ---------------------------------------------------------------------------
# B4: the terminator must be last
# ---------------------------------------------------------------------------


def test_b4_root_must_be_last_in_the_mro() -> None:
    """
    PriceRule.adjust does not call super(), so a mixin the MRO places after
    the root can never run.
    """

    class PlainMixin:  # deliberately not a PriceRule
        def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
            return running

    with pytest.raises(TypeError) as excinfo:

        class Bad(PercentTax, PlainMixin):
            pass

    message = str(excinfo.value)
    assert "PriceRule must be last in the MRO" in message
    assert "PlainMixin" in message


def test_b4_mixin_before_the_root_is_allowed() -> None:
    """Placed before the root it is live code, so there is nothing to reject."""

    class Checkout(Scaling, PriceRule):
        pass

    rule = Checkout(factor=Decimal(2))
    assert rule.price(Money("10.00", "USD")) == Money("20.00", "USD")


# ---------------------------------------------------------------------------
# R1: a PriceContext is a value, reusable across evaluations
# ---------------------------------------------------------------------------


def test_r1_one_context_drives_repeated_evaluations_identically() -> None:
    """
    The shadow used to be a mutable box inside the "frozen" PriceContext, so
    each adjust() rescaled it and the next call started from the last call's
    result: 103.55 -> 112.87 -> 123.03 off one context.
    """
    rule = TaxThenDiscount(**_config("USD"))  # type: ignore[arg-type]
    ctx = _ctx("USD", "100.00")
    results = [rule.adjust(ctx.start(), ctx=ctx) for _ in range(3)]
    assert results == [results[0]] * 3


def test_r1_adjust_does_not_mutate_its_inputs() -> None:
    """Purity, stated directly: neither the Running nor the context moves."""
    rule = TaxThenDiscount(**_config("USD"))  # type: ignore[arg-type]
    ctx = _ctx("USD", "100.00")
    running = ctx.start()

    out = rule.adjust(running, ctx=ctx)

    assert running == _run("100.00")
    assert ctx == _ctx("USD", "100.00")
    assert out is not running


def test_r1_replace_on_a_context_leaves_the_original_alone() -> None:
    """
    ``dataclasses.replace`` used to hand the copy the original's shadow box,
    and __post_init__ then reset the *original's* shadow through it.
    """
    first = _ctx("USD", "100.00")
    second = replace(first, base=Decimal("50.00"))

    assert first.base == Decimal("100.00")
    assert first.start() == _run("100.00")
    assert second.start() == _run("50.00")


def test_r1_price_is_repeatable() -> None:
    rule = TaxThenDiscount(**_config("USD", discount="30.00"))  # type: ignore[arg-type]
    prices = [rule.price(Money("100.00", "USD")) for _ in range(3)]
    assert prices == [Money("87.20", "USD")] * 3


def test_r1_context_and_running_are_frozen() -> None:
    ctx = _ctx("USD", "100.00")
    running = ctx.start()

    with pytest.raises(FrozenInstanceError):
        ctx.base = Decimal("1")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        running.undiscounted = Decimal("1")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# R2: every rule states how it treats both components
# ---------------------------------------------------------------------------


def test_r2_fixed_discount_carries_the_shadow_through_unchanged() -> None:
    """That is what makes it the "no discounts applied" amount."""

    class Only(FixedDiscount, PriceRule):
        pass

    out = Only(discount=Money("10.00", "USD")).adjust(
        _run("100.00"), ctx=_ctx("USD", "100.00")
    )
    assert out == _run("90.00", "100.00")


def test_r2_percent_tax_scales_both_components() -> None:
    """
    Kills: dropping the shadow scaling. Without it the cap would compare a
    taxed amount against an untaxed reference and bind far too early.
    """

    class Only(PercentTax, PriceRule):
        pass

    out = Only(tax_rate=TAX).adjust(_run("100.00"), ctx=_ctx("USD", "100.00"))
    assert out == _run("109.00", "109.00")


def test_r2_a_clamping_cap_preserves_the_shadow() -> None:
    class Only(DiscountCap, PriceRule):
        pass

    out = Only(max_discount_pct=Decimal("0.05")).adjust(
        _run("87.20", "109.00"), ctx=_ctx("USD", "100.00")
    )
    assert out == _run("103.55", "109.00")


def test_r2_every_shipped_rule_returns_a_running() -> None:
    """
    The type is the invariant's enforcement: a rule cannot return without
    naming both components, so a forgotten shadow is a visible omission
    rather than a side effect that never happened.
    """
    ctx = _ctx("USD", "100.00")
    rules = [
        FixedDiscount(discount=Money("1.00", "USD")),
        PercentTax(tax_rate=TAX),
        DiscountCap(max_discount_pct=CAP),
        PriceRule(),
    ]
    for rule in rules:
        assert isinstance(rule.adjust(ctx.start(), ctx=ctx), Running)


# ---------------------------------------------------------------------------
# R3: rounding is price()'s final step, not a rule
# ---------------------------------------------------------------------------


def test_r3_there_is_no_rounding_rule() -> None:
    assert not hasattr(pricing, "Rounding")


def test_r3_a_leaf_that_transforms_last_is_no_longer_a_hazard() -> None:
    """
    Under the old design this class was the B2 bug -- its transform ran after
    the Rounding mixin -- and validation had to reject it. Rounding now
    happens after adjust() returns, so the class is simply a valid pipeline
    and price() still delivers a well-formed Money.
    """

    class Leaf(PercentTax, PriceRule):
        def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
            result = super().adjust(running, ctx=ctx)
            return Running(result.amount / 3, result.undiscounted / 3)

    rule = Leaf(tax_rate=TAX)
    # 100.00 * 1.09 / 3 = 36.3333... -> quantized once, at the end.
    assert rule.price(Money("100.00", "USD")) == Money("36.33", "USD")


def test_r3_a_two_level_specialization_is_no_longer_a_false_positive() -> None:
    """
    ``class V3(V2(...))`` delegating down was rejected at class-definition
    time by the old structural check. There is nothing left to reject.
    """

    class V2(PercentTax):
        def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
            return super().adjust(running, ctx=ctx)

    class V3(V2):
        def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
            return super().adjust(running, ctx=ctx)

    class Checkout(V3, FixedDiscount, PriceRule):
        pass

    rule = Checkout(tax_rate=TAX, discount=Money("10.00", "USD"))
    assert rule.price(Money("100.00", "USD")) == Money("98.10", "USD")


def test_r3_rounding_is_half_even_not_half_up() -> None:
    """Kills: ROUND_HALF_EVEN -> ROUND_HALF_UP."""

    class Only(Scaling, PriceRule):
        pass

    assert Only(factor=Decimal("1.005")).price(Money("1.00", "USD")) == Money(
        "1.00", "USD"
    )
    assert Only(factor=Decimal("1.015")).price(Money("1.00", "USD")) == Money(
        "1.02", "USD"
    )


def test_r3_rounding_uses_the_currencys_minor_units() -> None:
    """Kills: a hard-coded 2 places, or the sign of the scaleb exponent."""

    class Only(Scaling, PriceRule):
        pass

    assert Only(factor=Decimal("0.986")).price(Money("100", "IRR")) == Money(
        "99", "IRR"
    )
    assert Only(factor=Decimal("1.23456")).price(Money("1.000", "KWD")) == Money(
        "1.235", "KWD"
    )


def test_r3_adjust_is_not_quantized_on_the_way_through() -> None:
    """
    Rounding once at the end means full precision inside the pipeline. A
    per-rule quantization would have lost the third decimal here.
    """

    class Only(Scaling, PriceRule):
        pass

    out = Only(factor=Decimal("1.005")).adjust(_run("1.00"), ctx=_ctx("USD", "1.00"))
    assert out.amount == Decimal("1.00500")


# ---------------------------------------------------------------------------
# R4: the accepted key set is resolved at class creation, not per instance
# ---------------------------------------------------------------------------


def test_r4_accepted_keys_are_a_class_attribute() -> None:
    assert DiscountThenTax._accepted_config_keys == frozenset(
        {"tax_rate", "max_discount_pct", "discount"}
    )
    assert PriceRule._accepted_config_keys == frozenset()


def test_r4_construction_does_not_run_the_linearizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    __new__ used to call linearize() on every construction, which was about
    80% of the cost of building a rule.
    """

    def explode(cls: type) -> tuple[type, ...]:
        raise AssertionError("linearize() must not run at construction time")

    monkeypatch.setattr(pricing, "linearize", explode)
    rule = DiscountThenTax(**_config("USD"))  # type: ignore[arg-type]
    assert rule.price(Money("100.00", "USD")) == Money("98.10", "USD")


def test_r4_a_new_subclass_still_gets_its_own_key_union() -> None:
    """The cache is per class, so it must not be inherited stale."""

    class Extra(PriceRule):
        _config_keys = frozenset({"extra"})

        def __init__(self, *, extra: object, **kw: Any) -> None:
            self.extra = extra
            super().__init__(**kw)

    class Checkout(Extra, PercentTax, PriceRule):
        pass

    assert Checkout._accepted_config_keys == frozenset({"extra", "tax_rate"})
    assert PercentTax._accepted_config_keys == frozenset({"tax_rate"})


# ---------------------------------------------------------------------------
# Mutation-killing tests for the remaining arithmetic
# ---------------------------------------------------------------------------


def test_mutation_fixed_discount_floors_at_exactly_zero() -> None:
    """Kills: dropping the floor, or returning the negative difference."""

    class Only(FixedDiscount, PriceRule):
        pass

    rule = Only(discount=Money("30.00", "USD"))
    out = rule.adjust(_run("10.00"), ctx=_ctx("USD", "10.00"))
    assert out.amount == Decimal(0)


def test_mutation_fixed_discount_subtracts_rather_than_adds() -> None:
    """Kills: subtract -> add."""

    class Only(FixedDiscount, PriceRule):
        pass

    rule = Only(discount=Money("10.00", "USD"))
    out = rule.adjust(_run("100.00"), ctx=_ctx("USD", "100.00"))
    assert out.amount == Decimal("90.00")


def test_mutation_percent_tax_multiplies_by_one_plus_rate() -> None:
    """Kills: (1 + rate) -> rate, which would return the tax, not the total."""

    class Only(PercentTax, PriceRule):
        pass

    out = Only(tax_rate=TAX).adjust(_run("100"), ctx=_ctx("USD", "100"))
    assert out.amount == Decimal("109.00")


def test_mutation_discount_cap_measures_against_the_shadow_not_the_base() -> None:
    """Kills: result.undiscounted -> ctx.base (this is B1 in miniature)."""
    ctx = _ctx("USD", "100.00")

    class Only(DiscountCap, PriceRule):
        pass

    rule = Only(max_discount_pct=Decimal("0.05"))
    # cap = 5% of 109.00 = 5.45 -> 103.55, not 100.00 - 5.00 = 95.00
    out = rule.adjust(_run("87.20", "109.00"), ctx=ctx)
    assert out.amount == Decimal("103.55")


def test_mutation_discount_cap_multiplies_to_get_the_cap() -> None:
    """Kills: reference * pct -> reference + pct or reference - pct."""

    class Only(DiscountCap, PriceRule):
        pass

    rule = Only(max_discount_pct=Decimal("0.05"))
    out = rule.adjust(_run("80", "100"), ctx=_ctx("USD", "100"))
    assert out.amount == Decimal("95.00")


def test_mutation_discount_cap_leaves_an_under_cap_discount_untouched() -> None:
    """Kills: clamping unconditionally."""

    class Only(DiscountCap, PriceRule):
        pass

    rule = Only(max_discount_pct=Decimal("0.20"))
    out = rule.adjust(_run("98", "100"), ctx=_ctx("USD", "100"))
    assert out.amount == Decimal("98")


def test_mutation_each_rule_calls_super_exactly_once() -> None:
    """Kills: a duplicated or missing super().adjust() anywhere in the chain."""
    seen: list[Decimal] = []

    class Counting(PriceRule):
        _config_keys = frozenset({"counter"})

        def __init__(self, *, counter: list[Decimal], **kw: Any) -> None:
            self.counter = counter
            super().__init__(**kw)

        def adjust(self, running: Running, *, ctx: PriceContext) -> Running:
            self.counter.append(running.amount)
            return super().adjust(running, ctx=ctx)

    class Counted(PercentTax, DiscountCap, FixedDiscount, Counting, PriceRule):
        pass

    rule = Counted(counter=seen, **_config("USD"))  # type: ignore[arg-type]
    rule.price(Money("100.00", "USD"))
    assert seen == [Decimal("100.00")]


def test_mutation_terminator_returns_its_argument_unchanged() -> None:
    """Kills: PriceRule.adjust returning anything but its argument."""
    running = _run("7.77")
    assert PriceRule().adjust(running, ctx=_ctx("USD", "7.77")) is running


def test_mutation_start_seeds_both_components_from_the_base() -> None:
    """Kills: seeding the shadow with zero, which would make every cap bind."""
    assert _ctx("USD", "100.00").start() == _run("100.00", "100.00")


# ---------------------------------------------------------------------------
# Context, currency and config
# ---------------------------------------------------------------------------


def test_pipeline_ignores_the_ambient_decimal_context() -> None:
    """
    The P1 finding: a hostile thread context must not move the price. Covers
    price() end to end, so it also covers Money construction at both ends --
    and, since R1, it reuses one rule across two calls.
    """
    rule = DiscountThenTax(**_config("USD"))  # type: ignore[arg-type]
    assert rule.price(Money("100.00", "USD")) == Money("98.10", "USD")

    with localcontext() as ambient:
        ambient.prec = 3
        ambient.rounding = ROUND_UP
        assert rule.price(Money("100.00", "USD")) == Money("98.10", "USD")


def test_context_rejects_a_non_decimal_base() -> None:
    with pytest.raises(TypeError, match="base must be a Decimal"):
        PriceContext(base=100.0, currency=Currency("USD"))  # type: ignore[arg-type]


def test_context_rejects_a_non_currency() -> None:
    with pytest.raises(TypeError, match="currency must be a Currency"):
        PriceContext(base=Decimal("100"), currency="USD")  # type: ignore[arg-type]


def test_currency_mismatch_is_loud() -> None:
    rule = DiscountThenTax(**_config("USD"))  # type: ignore[arg-type]
    with pytest.raises(CurrencyMismatch):
        rule.price(Money("100", "IRR"))


def test_unknown_config_key_fails_loudly_and_lists_valid_keys() -> None:
    config = _config("USD")
    config["taxrate"] = config.pop("tax_rate")

    with pytest.raises(UnknownPriceConfig) as excinfo:
        DiscountThenTax(**config)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "unknown config key(s) 'taxrate'" in message
    for key in ("'discount'", "'max_discount_pct'", "'tax_rate'"):
        assert key in message


def test_unknown_config_key_is_a_typeerror() -> None:
    assert issubclass(UnknownPriceConfig, TypeError)


def test_missing_config_key_is_a_plain_typeerror() -> None:
    config = _config("USD")
    del config["tax_rate"]

    with pytest.raises(TypeError, match="tax_rate"):
        DiscountThenTax(**config)  # type: ignore[arg-type]


def test_float_rates_are_rejected() -> None:
    with pytest.raises(TypeError, match="tax_rate must be a Decimal"):
        PercentTax(tax_rate=0.09)  # type: ignore[arg-type]


def test_cap_percentage_must_be_a_fraction() -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        DiscountCap(max_discount_pct=Decimal("1.5"))


def test_negative_discount_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        FixedDiscount(discount=Money("-1.00", "USD"))
