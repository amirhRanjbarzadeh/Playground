import gc
import weakref

from stock_watcher import InventoryItem, StockWatcher


def test_set_qty_updates_quantity() -> None:
    item = InventoryItem("ABC123", 10)

    item.set_qty(5)

    assert item.qty == 5


def test_inventory_item_notifies_all_watchers() -> None:
    item = InventoryItem("ABC123", 10)
    watcher1 = StockWatcher("Watcher 1")
    watcher2 = StockWatcher("Watcher 2")
    item.add_watcher(watcher1)
    item.add_watcher(watcher2)

    item.set_qty(5)

    assert watcher1.log == [("ABC123", 5)]
    assert watcher2.log == [("ABC123", 5)]


def test_adding_same_watcher_twice_notifies_once() -> None:
    item = InventoryItem("ABC123", 10)
    watcher = StockWatcher("Watcher")
    item.add_watcher(watcher)
    item.add_watcher(watcher)

    item.set_qty(5)

    assert watcher.log == [("ABC123", 5)]


def test_deleted_watcher_is_dropped_and_others_still_notified() -> None:
    item = InventoryItem("ABC123", 10)
    watcher1 = StockWatcher("Watcher 1")
    watcher2 = StockWatcher("Watcher 2")
    item.add_watcher(watcher1)
    item.add_watcher(watcher2)
    item.set_qty(5)

    del watcher1
    gc.collect()  # not needed on CPython, but makes the test portable
    item.set_qty(2)

    assert len(item.watchers) == 1
    assert watcher2.log == [("ABC123", 5), ("ABC123", 2)]


def test_item_does_not_keep_watcher_alive() -> None:
    item = InventoryItem("ABC123", 10)
    watcher = StockWatcher("Watcher")
    item.add_watcher(watcher)
    ref = weakref.ref(watcher)

    del watcher
    gc.collect()

    assert ref() is None
    assert len(item.watchers) == 0
