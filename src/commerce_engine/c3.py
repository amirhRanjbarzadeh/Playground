from __future__ import annotations

import builtins
import collections.abc
import io
import numbers
from types import ModuleType

# ---------------------------------------------------------------------------
# CPython-compatible error formatting
# ---------------------------------------------------------------------------


def _probe_prefix() -> str:
    """
    Ask the running CPython interpreter for its MRO-error wording once.

    Python 3.11/3.12:
        Cannot create a consistent method resolution
        order (MRO) for bases

    Python 3.13:
        Cannot create a consistent method resolution order (MRO) for bases

    We let the running interpreter determine that formatting.
    """
    A = type("A", (), {})
    B = type("B", (A,), {})

    try:
        type("C", (A, B), {})
    except TypeError as exc:
        msg = str(exc)
    else:
        raise AssertionError("expected inconsistent MRO")

    return msg[: msg.rindex("A, B")]


_MRO_PREFIX = _probe_prefix()


def _mro_error(heads: list[type]) -> TypeError:
    return TypeError(_MRO_PREFIX + ", ".join(head.__name__ for head in heads))


def _mro_error_names(heads: list[str]) -> TypeError:
    return TypeError(_MRO_PREFIX + ", ".join(heads))


# ---------------------------------------------------------------------------
# Real Python classes
# ---------------------------------------------------------------------------


def _duplicate_base(bases: tuple[type, ...]) -> type | None:
    """
    Return the first duplicated direct base.

    Classes are deliberately compared by identity rather than equality:
    C3 is concerned with class objects themselves.
    """
    for i, base in enumerate(bases):
        if any(base is previous for previous in bases[:i]):
            return base

    return None


def _merge(seqs: list[list[type]]) -> tuple[type, ...]:
    """
    Perform the C3 merge.

    A candidate is valid iff it is the head of one sequence and does not
    occur in the tail of any remaining sequence.
    """

    # M1:
    # merge owns its working lists. In particular, callers may construct
    # these sequences from memoized parent MROs, so mutating the caller's
    # lists would corrupt cached results.
    seqs = [list(seq) for seq in seqs]

    result: list[type] = []

    while True:
        # Empty sequences no longer constrain the merge.
        seqs = [seq for seq in seqs if seq]

        if not seqs:
            return tuple(result)

        candidate: type | None = None

        # Restart from the first sequence after every successful selection.
        for seq in seqs:
            head = seq[0]

            # M4:
            # Compare classes by identity throughout. Using `in` here would
            # invoke equality and could disagree with the identity-based
            # removal below for classes with unusual metaclasses.
            blocked = any(any(head is item for item in other[1:]) for other in seqs)

            if not blocked:
                candidate = head
                break

        if candidate is None:
            # M2:
            # CPython reports the unique heads of the remaining non-empty
            # sequences, preserving first occurrence / sequence order.
            heads: list[type] = []

            for seq in seqs:
                head = seq[0]

                if not any(head is existing for existing in heads):
                    heads.append(head)

            raise _mro_error(heads)

        result.append(candidate)

        # Remove the chosen candidate only when it is the current head.
        for seq in seqs:
            if seq and seq[0] is candidate:
                seq.pop(0)


def linearize(cls: type) -> tuple[type, ...]:
    """
    Recompute cls's MRO from ``__bases__`` alone using C3.

    Reads only ``__bases__``, transitively. It never consults ``cls.__mro__``
    or calls ``cls.mro()``, which is the point: the result is an independent
    computation that can be compared against CPython's.

    C3:
    Scope -- what this function does *not* model:

    * A metaclass that overrides ``mro()``. CPython calls that override and
      will happily accept whatever sequence it returns, so for such a class
      ``linearize(C) != C.__mro__`` is expected and correct. C3 is the
      default policy, not the only one.
    * ``__mro_entries__`` / :pep:`560` generic aliases. By the time a class
      object exists its ``__bases__`` are already resolved to real classes,
      so this only matters if you hand the function something that is not a
      class.
    * Virtual subclasses registered with ``abc.ABCMeta.register``. They
      affect ``isinstance``, never the MRO.

    Raises TypeError with CPython's own wording for an inconsistent
    hierarchy, and ``duplicate base class X`` for a repeated direct base.
    """

    # C5:
    # Keyed by id(), not by the class object. Everything else in this module
    # compares classes by identity (see M4), and a dict keyed by the class
    # would instead go through the metaclass's __hash__/__eq__ -- which a
    # metaclass is free to override, or to make unhashable, at which point
    # a perfectly ordinary lookup raises or silently conflates two distinct
    # classes. `keepalive` holds a reference so no id can be recycled while
    # the memo is live.
    memo: dict[int, tuple[type, ...]] = {}
    keepalive: list[type] = []

    def visit(c: type) -> tuple[type, ...]:
        cached = memo.get(id(c))

        if cached is not None:
            return cached

        keepalive.append(c)

        # Explicit root / terminator.
        if c is object:
            result: tuple[type, ...] = (object,)
            memo[id(c)] = result
            return result

        bases = c.__bases__

        # M3:
        # Duplicate direct bases are rejected before C3 merging.
        duplicate = _duplicate_base(bases)

        if duplicate is not None:
            raise TypeError(f"duplicate base class {duplicate.__name__}")

        parent_mros = [visit(base) for base in bases]

        # C3:
        #
        # L[C] =
        #     [C] +
        #     merge(
        #         L[B1],
        #         L[B2],
        #         ...,
        #         [B1, B2, ...],
        #     )
        merged = _merge(
            [
                *[list(parent_mro) for parent_mro in parent_mros],
                list(bases),
            ]
        )

        result = (c, *merged)

        # Memoization is valid because, during this traversal, L(C) is
        # completely determined by C.__bases__ and the linearizations of
        # those bases. Nothing here mutates a class, so within one call a
        # given C cannot change its answer.
        #
        # What would invalidate it is assignment to __bases__, which changes
        # the MRO of that class and of every descendant.
        #
        # C4:
        # That assignment is narrower than it looks. CPython refuses it
        # unless the old and new bases share a compatible instance layout --
        # the "solid base" rule -- so it is rejected outright for most
        # static/extension types (object, int, type, ...) and for anything
        # whose __basicsize__ or __slots__ would shift. In practice it is
        # reachable for ordinary heap types whose layout does not move.
        #
        # Narrow is not never, though, and a rebind needs no cooperation from
        # us: it can happen between two calls, from another thread, in a
        # decorator. So the memo deliberately belongs to this single
        # linearize() call. A module-level cache would need invalidation
        # machinery with no event to hang it on, since __bases__ assignment
        # is not observable from Python.
        memo[id(c)] = result

        return result

    return visit(cls)


# ---------------------------------------------------------------------------
# Plain graph version
# ---------------------------------------------------------------------------


def _duplicate_name(bases: list[str]) -> str | None:
    """Return the first duplicate base name."""
    seen: set[str] = set()

    for base in bases:
        if base in seen:
            return base

        seen.add(base)

    return None


def _merge_graph(seqs: list[list[str]]) -> list[str]:
    """
    Same C3 merge algorithm, operating on class names instead of types.
    """

    # Own the working copies for the same reason as _merge().
    seqs = [list(seq) for seq in seqs]

    result: list[str] = []

    while True:
        seqs = [seq for seq in seqs if seq]

        if not seqs:
            return result

        candidate: str | None = None

        for seq in seqs:
            head = seq[0]

            blocked = any(head in other[1:] for other in seqs)

            if not blocked:
                candidate = head
                break

        if candidate is None:
            # Unique stuck heads in first-occurrence order.
            heads: list[str] = []

            for seq in seqs:
                head = seq[0]

                if head not in heads:
                    heads.append(head)

            raise _mro_error_names(heads)

        result.append(candidate)

        for seq in seqs:
            if seq and seq[0] == candidate:
                seq.pop(0)


def linearize_graph(
    graph: dict[str, list[str]],
    name: str,
) -> list[str]:
    """
    Compute C3 on a plain dict of:

        class name -> direct base names

    A node omitted from the graph implicitly inherits from object.

    Example:

        graph = {
            "A": [],
            "B": ["A"],
            "C": ["A"],
            "D": ["B", "C"],
        }

        linearize_graph(graph, "D")

        -> ["D", "B", "C", "A", "object"]
    """

    memo: dict[str, list[str]] = {}

    # C2:
    # A dict of names, unlike real classes, can describe a cycle: CPython
    # refuses to build one, but nothing stops a caller writing
    # {"A": ["B"], "B": ["A"]}. Without this the recursion below just runs
    # until the stack dies with a RecursionError that names neither the
    # cycle nor the graph. `path` is the chain of nodes currently being
    # visited, so the error can print the loop it actually found.
    path: list[str] = []
    on_path: set[str] = set()

    def visit(node: str) -> list[str]:
        if node in memo:
            # Do not expose the memo's mutable list to callers/merge.
            return memo[node].copy()

        if node in on_path:
            cycle = [*path[path.index(node) :], node]
            raise TypeError(f"cyclic inheritance: {' -> '.join(cycle)}")

        path.append(node)
        on_path.add(node)

        if node == "object":
            result = ["object"]
            memo[node] = result
            path.pop()
            on_path.discard(node)
            return result.copy()

        # Explicit [] means "no explicitly named base", which is equivalent
        # to inheriting object in Python:
        #
        #     class A:
        #         pass
        #
        # -> A(object)
        #
        # Missing nodes get the same implicit-object behavior.
        bases = list(graph.get(node, []))

        if not bases:
            bases = ["object"]

        duplicate = _duplicate_name(bases)

        if duplicate is not None:
            raise TypeError(f"duplicate base class {duplicate}")

        parent_mros = [visit(base) for base in bases]

        merged = _merge_graph(
            [
                *parent_mros,
                bases,
            ]
        )

        result = [
            node,
            *merged,
        ]

        # As above, memoization is valid only while the graph remains
        # unchanged. Keeping this memo local to one call avoids stale
        # results if the caller later mutates the graph.
        memo[node] = result
        path.pop()
        on_path.discard(node)

        return result.copy()

    return visit(name)


# ---------------------------------------------------------------------------
# Stdlib verification
# ---------------------------------------------------------------------------


def _module_classes(module: ModuleType) -> set[type]:
    """
    Collect every class exposed directly in a module's namespace.
    """
    return {value for value in vars(module).values() if isinstance(value, type)}


def _all_exception_classes() -> set[type]:
    """
    Walk the currently loaded exception hierarchy starting at BaseException.
    """
    result: set[type] = set()
    stack = [BaseException]

    while stack:
        cls = stack.pop()

        if cls in result:
            continue

        result.add(cls)
        stack.extend(cls.__subclasses__())

    return result


def _project_modules() -> list[ModuleType]:
    """
    Our own modules. Imported lazily inside the function because pricing.py
    imports linearize() from this module; importing it at module scope would
    be a cycle.
    """
    from commerce_engine import money, payments, pricing

    return [money, payments, pricing]


def stdlib_sweep() -> None:
    """
    Compare linearize(C) with C.__mro__ for the required stdlib classes and
    for our own money / payments / pricing classes.
    """
    classes: set[type] = set()

    for module in (
        builtins,
        collections.abc,
        io,
        numbers,
        *_project_modules(),
    ):
        classes.update(_module_classes(module))

    classes.update(_all_exception_classes())

    failures: list[
        tuple[
            type,
            tuple[type, ...],
            tuple[type, ...],
        ]
    ] = []

    for cls in classes:
        expected = cls.__mro__
        actual = linearize(cls)

        if actual != expected:
            failures.append((cls, expected, actual))

    if failures:
        lines: list[str] = []

        for cls, expected, actual in failures:
            lines.append(
                f"{cls.__module__}.{cls.__qualname__}\n"
                f"  expected: {expected}\n"
                f"  actual:   {actual}"
            )

        raise AssertionError(
            f"{len(failures)} MRO mismatch(es):\n\n" + "\n\n".join(lines)
        )

    print(f"{len(classes)} classes, 0 mismatches")


# ---------------------------------------------------------------------------
# Basic self-tests
# ---------------------------------------------------------------------------


def _self_test() -> None:
    class A:
        pass

    class B(A):
        pass

    class C(A):
        pass

    class D(B, C):
        pass

    assert linearize(A) == A.__mro__
    assert linearize(B) == B.__mro__
    assert linearize(C) == C.__mro__
    assert linearize(D) == D.__mro__

    graph = {
        "A": [],
        "B": ["A"],
        "C": ["A"],
        "D": ["B", "C"],
    }

    assert linearize_graph(
        graph,
        "D",
    ) == [
        "D",
        "B",
        "C",
        "A",
        "object",
    ]

    # Duplicate-base failure.
    try:
        linearize_graph(
            {"C": ["A", "A"]},
            "C",
        )
    except TypeError as exc:
        assert str(exc) == "duplicate base class A"
    else:
        raise AssertionError("duplicate base should have failed")


if __name__ == "__main__":
    _self_test()
    stdlib_sweep()
