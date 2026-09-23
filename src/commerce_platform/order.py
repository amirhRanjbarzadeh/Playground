from __future__ import annotations

from typing import Any


class Order:
    def __init__(self, items: list[Any], total: int) -> None:
        self._placed = False
        self.items = items
        self.total = total

    def place(self) -> None:
        self._placed = True

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_placed", False) and not name.startswith("_"):
            raise AttributeError(f"cannot modify '{name}' on a placed order")
        object.__setattr__(self, name, value)
