import pytest


class Entity:
    created = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        Entity.created += 1
        self.id = Entity.created


class AuditMixin(Entity):
    def __init__(self, created_by="system", **kwars):
        super().__init__(**kwars)
        self.created_by = created_by


class PricedMixin(Entity):
    def __init__(self, currency="EUR", **kwars):
        super().__init__(**kwars)
        self.currency = currency


class Product(AuditMixin, PricedMixin):
    def __init__(self, name, **kwars):
        super().__init__(**kwars)
        self.name = name


def test_valid_product():
    before = Entity.created
    p = Product("mug", currency="IRR")
    assert p.name == "mug"
    assert p.currency == "IRR"
    assert p.created_by == "system"
    assert Entity.created == before + 1
    assert p.id == Entity.created


def test_invalid_product():
    with pytest.raises(TypeError):
        Product("mug", color="red")