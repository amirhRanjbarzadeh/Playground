class Order:
    def __init__(self, items, total):
        self._placed = False
        self.items = items
        self.total = total

    def place(self):
        self._placed = True

    def __setattr__(self, name, value):
        if getattr(self, '_placed', False) and not name.startswith('_'):
            raise AttributeError(f"cannot modify '{name}' on a placed order")
        object.__setattr__(self, name, value)