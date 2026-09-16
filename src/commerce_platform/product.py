from decimal import Decimal


class Product:
    def __init__(self, name: str, price_cents: int) -> None:
        self.name = name
        self._price_cents = price_cents

    @property
    def price(self) -> Decimal:
        return Decimal(self._price_cents) / Decimal(100)

    @price.setter
    def price(self, value: int | float | Decimal) -> None:
        if not isinstance(value, (int, float, Decimal)):
            raise ValueError("Price must be a number")

        # str() first: Decimal(0.1) would carry the float's binary error.
        if isinstance(value, float):
            value = Decimal(str(value))
        else:
            value = Decimal(value)

        if value < 0:
            raise ValueError("Price cannot be negative")

        cents = value * Decimal(100)

        if cents != cents.to_integral_value():
            raise ValueError("Price cannot have sub-cent precision")

        self._price_cents = int(cents)

    @property
    def price_display(self) -> str:
        return f"${self.price:.2f}"
