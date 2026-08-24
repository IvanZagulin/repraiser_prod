from unittest.mock import patch

from app.models.product import Product
from app.models.wb_catalog import WBCatalog


def _catalog_card(test_account, nm_id=1229563360, image_url="https://basket.example/card.webp"):
    return WBCatalog(
        account_id=test_account.id,
        nm_id=nm_id,
        vendor_code="CACHE-SKU",
        name="Cached WB product",
        image_url=image_url,
        subject_name="Books",
    )


def test_search_wb_returns_photo_from_nightly_catalog(client, db_session, test_account):
    db_session.add(_catalog_card(test_account))
    db_session.commit()

    with patch("app.api.products.create_wb_client") as wb_client_factory:
        response = client.get(
            "/api/products/search-wb",
            params={"account_id": test_account.id, "text": "1229563360"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "nm_id": 1229563360,
        "seller_sku": "CACHE-SKU",
        "name": "Cached WB product",
        "image_url": "https://basket.example/card.webp",
    }
    wb_client_factory.assert_not_called()


def test_create_product_reuses_catalog_photo_without_wb_request(
    client, db_session, test_account
):
    db_session.add(_catalog_card(test_account))
    db_session.commit()

    with patch("app.api.products.create_wb_client") as wb_client_factory:
        response = client.post(
            "/api/products",
            json={"account_id": test_account.id, "nm_id": 1229563360},
        )

    assert response.status_code == 201
    assert response.json()["image_url"] == "https://basket.example/card.webp"
    wb_client_factory.assert_not_called()

    product = db_session.query(Product).filter(Product.nm_id == 1229563360).one()
    assert product.seller_sku == "CACHE-SKU"
    assert product.name == "Cached WB product"
    assert product.image_url == "https://basket.example/card.webp"
    assert product.subject_name == "Books"


def test_create_product_does_not_fetch_photo_synchronously_when_cache_has_no_photo(
    client, db_session, test_account
):
    with patch("app.api.products.create_wb_client") as wb_client_factory:
        response = client.post(
            "/api/products",
            json={
                "account_id": test_account.id,
                "nm_id": 1229563361,
                "seller_sku": "READY-SKU",
                "name": "Already found product",
            },
        )

    assert response.status_code == 201
    assert response.json()["image_url"] is None
    wb_client_factory.assert_not_called()
