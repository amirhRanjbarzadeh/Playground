from __future__ import annotations

from typing import Any, ClassVar

__all__ = ["PaymentGateway"]


class PaymentGateway:
    code: ClassVar[str]
    _registry: ClassVar[dict[str, type[PaymentGateway]]] = {}

    def __init_subclass__(cls, *, code: str, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        if not isinstance(code, str) or not code.strip():
            raise TypeError(f"{cls.__qualname__}: code must be a non-empty str")
        if not callable(getattr(cls, "charge", None)):
            raise TypeError(f"{cls.__qualname__} must define a callable charge()")

        existing = PaymentGateway._registry.get(code)
        if existing is not None:
            raise TypeError(
                f"gateway code {code!r} is already registered to "
                f"{existing.__qualname__}; cannot register {cls.__qualname__}"
            )

        cls.code = code
        PaymentGateway._registry[code] = cls

    @classmethod
    def for_code(cls, code: str) -> type[PaymentGateway]:
        try:
            return PaymentGateway._registry[code]
        except KeyError:
            known = ", ".join(sorted(PaymentGateway._registry)) or "<none>"
            raise ValueError(
                f"unknown gateway code {code!r}; known codes: {known}"
            ) from None
