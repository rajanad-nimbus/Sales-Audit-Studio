import pytest

from apps.ingest.shopify import adapter, reference_sync as rs


def test_shop_and_location_become_stores():
    shop = rs.shop_to_store({"id": 1, "name": "Nimbus Test", "currency": "USD"}, "SHOPIFY-MAIN")
    assert (shop["category"], shop["code"], shop["name"]) == ("Store", "SHOPIFY-MAIN", "Nimbus Test")
    loc = rs.location_to_store({"id": 77, "name": "Warehouse", "active": False, "country_code": "US"})
    assert (loc["code"], loc["active"], loc["attributes"]["country"]) == ("SHOPIFY-LOC-77", False, "US")


def test_each_variant_becomes_an_item_keyed_by_sku():
    items = rs.product_to_items({"id": 5, "title": "Shirt", "status": "active", "variants": [
        {"id": 1, "sku": "SH-S", "title": "Small", "price": "10.00"},
        {"id": 2, "sku": "", "title": "Default Title", "price": "12.00"}]})
    assert [(i["code"], i["name"]) for i in items] == [("SH-S", "Shirt - Small"), ("SHOPIFY-VAR-2", "Shirt")]
    assert all(i["active"] for i in items)


def test_archived_product_items_are_inactive():
    items = rs.product_to_items({"id": 6, "title": "Old", "status": "archived", "variants": [{"id": 3, "sku": "O"}]})
    assert items[0]["active"] is False


def test_shopify_reference_mapping_uses_stable_fallback_names():
    shop = rs.shop_to_store({"id": 1, "name": "   "}, "SHOPIFY-MAIN")
    location = rs.location_to_store({"id": 77, "name": "  "})
    item = rs.product_to_items({
        "id": 5, "title": "  ", "variants": [{"id": 3, "sku": "SKU-3"}],
    })[0]

    assert shop["name"] == "SHOPIFY-MAIN"
    assert location["name"] == "Location 77"
    assert item["name"] == "Product 5"


@pytest.mark.parametrize("mapping", [
    lambda: rs.shop_to_store({"id": 1, "name": "Shop"}, "  "),
    lambda: rs.location_to_store({"name": "Location"}),
    lambda: rs.product_to_items({"title": "Product", "variants": [{"sku": "SKU"}]}),
])
def test_shopify_reference_mapping_rejects_missing_identity(mapping):
    with pytest.raises(adapter.ShopifyAPIError):
        mapping()
