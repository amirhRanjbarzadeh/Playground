from __future__ import annotations

import builtins
import collections.abc
import io
import numbers

import pytest

from commerce_engine import money, payments, pricing
from commerce_engine.c3 import (
    _all_exception_classes,
    _module_classes,
    linearize,
    linearize_graph,
)


def _sweep_classes() -> list[type]:
    classes: set[type] = set()
    for module in (builtins, collections.abc, io, numbers, money, payments, pricing):
        classes.update(_module_classes(module))
    classes.update(_all_exception_classes())
    return sorted(classes, key=lambda c: (c.__module__, c.__qualname__))


@pytest.mark.parametrize("cls", _sweep_classes(), ids=lambda c: c.__qualname__)
def test_linearize_matches_cpython(cls: type) -> None:
    assert linearize(cls) == cls.__mro__


def test_diamond() -> None:
    class A:
        pass

    class B(A):
        pass

    class C(A):
        pass

    class D(B, C):
        pass

    assert linearize(D) == D.__mro__


def test_graph_diamond() -> None:
    graph = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
    assert linearize_graph(graph, "D") == ["D", "B", "C", "A", "object"]


def test_graph_missing_node_is_implicitly_object() -> None:
    assert linearize_graph({"B": ["A"]}, "B") == ["B", "A", "object"]


def test_duplicate_base_message() -> None:
    with pytest.raises(TypeError, match=r"^duplicate base class A$"):
        linearize_graph({"C": ["A", "A"]}, "C")


def _cpython_mro_error() -> str:
    """The message CPython itself produces for an unorderable pair of bases."""

    class A:
        pass

    class B(A):
        pass

    with pytest.raises(TypeError) as excinfo:
        type("X", (A, B), {})
    return str(excinfo.value)


def test_inconsistent_order_message_matches_cpython_byte_for_byte() -> None:
    expected = _cpython_mro_error()

    with pytest.raises(TypeError) as excinfo:
        # A before B is unorderable exactly as `class X(A, B)` is, since B
        # already precedes A in its own linearization.
        linearize_graph({"A": [], "B": ["A"], "X": ["A", "B"]}, "X")

    assert str(excinfo.value) == expected


def test_stuck_bases_keep_their_order() -> None:
    """The message lists the stuck heads in sequence order, not sorted."""
    with pytest.raises(TypeError) as excinfo:
        linearize_graph({"A": [], "B": ["A"], "X": ["A", "B"]}, "X")

    assert str(excinfo.value).endswith("A, B")


def test_linearize_follows_reassigned_bases() -> None:
    """
    The memo lives for one call only, so a later __bases__ assignment is
    picked up rather than served from a stale cache.
    """

    class A:
        pass

    class B:
        pass

    class C(A):
        pass

    assert linearize(C) == (C, A, object)
    C.__bases__ = (B,)
    assert linearize(C) == (C, B, object)
    assert linearize(C) == C.__mro__


# ---------------------------------------------------------------------------
# C2: a name graph, unlike real classes, can describe a cycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("graph", "start", "expected"),
    [
        ({"A": ["B"], "B": ["A"]}, "A", "A -> B -> A"),
        ({"A": ["A"]}, "A", "A -> A"),
        ({"A": ["B"], "B": ["C"], "C": ["A"]}, "A", "A -> B -> C -> A"),
        ({"A": ["B"], "B": ["C"], "C": ["B"]}, "A", "B -> C -> B"),
    ],
)
def test_c2_cycles_raise_instead_of_exhausting_the_stack(
    graph: dict[str, list[str]], start: str, expected: str
) -> None:
    with pytest.raises(TypeError, match="cyclic inheritance"):
        linearize_graph(graph, start)

    with pytest.raises(TypeError) as excinfo:
        linearize_graph(graph, start)
    assert str(excinfo.value) == f"cyclic inheritance: {expected}"


def test_c2_acyclic_graphs_are_unaffected() -> None:
    graph = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
    assert linearize_graph(graph, "D") == ["D", "B", "C", "A", "object"]


def test_c2_a_diamond_is_not_mistaken_for_a_cycle() -> None:
    """B and C both reach A; visiting A twice is reuse, not a loop."""
    graph = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"], "E": ["D", "C"]}
    assert linearize_graph(graph, "E") == ["E", "D", "B", "C", "A", "object"]


# ---------------------------------------------------------------------------
# C3: documented scope
# ---------------------------------------------------------------------------


def test_c3_metaclass_mro_override_is_out_of_scope() -> None:
    """
    A metaclass may return any sequence from mro(); CPython uses it verbatim.
    linearize computes the C3 answer, so the two are expected to diverge --
    the docstring says so, and this pins that they really do.
    """

    class Reversing(type):
        def mro(cls) -> list[type]:
            return [cls, object]

    class Base:
        pass

    class Odd(Base, metaclass=Reversing):
        pass

    assert Odd.__mro__ == (Odd, object)
    assert linearize(Odd) == (Odd, Base, object)
    assert linearize(Odd) != Odd.__mro__


def test_c3_does_not_consult_mro_or_dunder_mro() -> None:
    """linearize reads __bases__ only, so a lying mro() cannot influence it."""

    class Liar(type):
        def mro(cls) -> list[type]:
            return [cls, object]

    class A:
        pass

    class B(A, metaclass=Liar):
        pass

    assert linearize(B)[1] is A


# ---------------------------------------------------------------------------
# C4 / C5: memo lifetime and keying
# ---------------------------------------------------------------------------


def test_c4_memo_does_not_outlive_a_bases_reassignment() -> None:
    """
    The memo is per-call precisely because __bases__ can be rebound between
    calls, with no event we could hang invalidation on.
    """

    class A:
        pass

    class B:
        pass

    class C(A):
        pass

    assert linearize(C) == (C, A, object)
    C.__bases__ = (B,)
    assert linearize(C) == (C, B, object)
    assert linearize(C) == C.__mro__


def test_c5_memo_survives_a_metaclass_with_hostile_eq_and_hash() -> None:
    """
    Keying the memo by the class object would route every lookup through the
    metaclass's __eq__/__hash__. This metaclass makes classes unhashable and
    equal to everything, which a dict[type, ...] cannot survive.
    """

    class HostileMeta(type):
        def __eq__(cls, other: object) -> bool:
            return True

        __hash__ = None  # type: ignore[assignment]

    class A(metaclass=HostileMeta):
        pass

    class B(A, metaclass=HostileMeta):
        pass

    class C(A, metaclass=HostileMeta):
        pass

    class D(B, C, metaclass=HostileMeta):
        pass

    with pytest.raises(TypeError):
        {D: 1}  # noqa: B018  # the class really is unhashable

    result = linearize(D)
    assert [entry.__name__ for entry in result] == ["D", "B", "C", "A", "object"]
    assert all(
        actual is expected for actual, expected in zip(result, D.__mro__, strict=True)
    )


def test_c5_distinct_classes_sharing_a_name_are_kept_apart() -> None:
    """Identity, not name or equality, is what distinguishes memo entries."""

    def make() -> type:
        class Same:
            pass

        return Same

    first, second = make(), make()
    assert first is not second
    assert first.__name__ == second.__name__

    child = type("Child", (first, second), {})

    assert linearize(child) == (child, first, second, object)
