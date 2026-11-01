import pytest
from apps.ingest.shopify import adapter
from apps.ingest.shopify import reference_sync as rs


def test_shop_and_location_become_stores():
    shop = rs.shop_to_store({"id": 1, "name": "Nimbus Test", "currency": "USD"}, "SHOPIFY-MAIN")
    assert (shop["category"], shop["code"], shop["name"]) == (
        "Store",
        "SHOPIFY-MAIN",
        "Nimbus Test",
    )
    loc = rs.location_to_store(
        {"id": 77, "name": "Warehouse", "active": False, "country_code": "US"}
    )
    assert (loc["code"], loc["active"], loc["attributes"]["country"]) == (
        "SHOPIFY-LOC-77",
        False,
        "US",
    )


def test_each_variant_becomes_an_item_keyed_by_sku():
    items = rs.product_to_items(
        {
            "id": 5,
            "title": "Shirt",
            "status": "active",
            "variants": [
                {"id": 1, "sku": "SH-S", "title": "Small", "price": "10.00"},
                {"id": 2, "sku": "", "title": "Default Title", "price": "12.00"},
            ],
        }
    )
    assert [(i["code"], i["name"]) for i in items] == [
        ("SH-S", "Shirt - Small"),
        ("SHOPIFY-VAR-2", "Shirt"),
    ]
    assert all(i["active"] for i in items)


@pytest.mark.parametrize("status", ["archived", "draft"])
def test_non_active_product_items_are_inactive(status):
    items = rs.product_to_items(
        {"id": 6, "title": "Old", "status": status, "variants": [{"id": 3, "sku": "O"}]}
    )
    assert items[0]["active"] is False


def test_shopify_reference_mapping_uses_stable_fallback_names():
    shop = rs.shop_to_store({"id": 1, "name": "   "}, "SHOPIFY-MAIN")
    location = rs.location_to_store({"id": 77, "name": "  ", "active": True})
    item = rs.product_to_items(
        {
            "id": 5,
            "title": "  ",
            "status": "active",
            "variants": [{"id": 3, "sku": "SKU-3"}],
        }
    )[0]

    assert shop["name"] == "SHOPIFY-MAIN"
    assert location["name"] == "Location 77"
    assert item["name"] == "Product 5"


@pytest.mark.parametrize(
    "mapping",
    [
        lambda: rs.shop_to_store({"id": 1, "name": "Shop"}, "  "),
        lambda: rs.location_to_store({"name": "Location", "active": True}),
        lambda: rs.product_to_items(
            {"title": "Product", "status": "active", "variants": [{"sku": "SKU"}]}
        ),
    ],
)
def test_shopify_reference_mapping_rejects_missing_identity(mapping):
    with pytest.raises(adapter.ShopifyAPIError):
        mapping()


@pytest.mark.parametrize("resource", ["shop", "location", "product", "variant"])
@pytest.mark.parametrize("identifier", [None, True, 0, -1, "invalid", {}, [1], 1 << 64])
def test_shopify_reference_mapping_rejects_invalid_identifiers(resource, identifier):
    with pytest.raises(adapter.ShopifyAPIError, match="invalid .* response"):
        if resource == "shop":
            rs.shop_to_store({"id": identifier}, "SHOPIFY-MAIN")
        elif resource == "location":
            rs.location_to_store({"id": identifier, "active": True})
        else:
            product = {
                "id": identifier if resource == "product" else 5,
                "status": "active",
                "variants": [{"id": identifier if resource == "variant" else 3}],
            }
            rs.product_to_items(product)


@pytest.mark.parametrize("active", [None, "false", 0, 1])
def test_location_requires_boolean_active_state(active):
    with pytest.raises(adapter.ShopifyAPIError, match="invalid locations response"):
        rs.location_to_store({"id": 77, "name": "Location", "active": active})


@pytest.mark.parametrize(
    "product",
    [
        {"id": 5, "status": "active"},
        {"id": 5, "status": "active", "variants": None},
        {"id": 5, "status": "active", "variants": []},
        {"id": 5, "status": "active", "variants": {}},
        {"id": 5, "status": "active", "variants": [None]},
        {"id": 5, "status": "active", "variants": [{"id": 3, "sku": ["invalid"]}]},
        {"id": 5, "status": "active", "variants": [{"id": 3, "title": {"invalid": True}}]},
        {"id": 5, "variants": [{"id": 3}]},
        {"id": 5, "status": "unknown", "variants": [{"id": 3}]},
    ],
)
def test_product_requires_complete_variant_and_status_data(product):
    with pytest.raises(adapter.ShopifyAPIError, match="invalid products response"):
        rs.product_to_items(product)
