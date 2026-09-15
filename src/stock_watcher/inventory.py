import weakref


class StockWatcher:
    def __init__(self, name: str) -> None:
        self.name = name
        self.log: list[tuple[str, int]] = []

    def notify(self, sku: str, new_qty: int) -> None:
        self.log.append((sku, new_qty))


class InventoryItem:
    def __init__(self, sku: str, qty: int) -> None:
        self.sku = sku
        self.qty = qty
        # Weak references: an item must not keep a watcher alive.
        self.watchers: weakref.WeakSet[StockWatcher] = weakref.WeakSet()

    def add_watcher(self, watcher: StockWatcher) -> None:
        self.watchers.add(watcher)

    def set_qty(self, new_qty: int) -> None:
        self.qty = new_qty
        for watcher in list(self.watchers):
            watcher.notify(self.sku, new_qty)
