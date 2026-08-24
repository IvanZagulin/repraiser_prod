import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
                "image_url": "https://basket-46.wbbasket.ru/card.webp",
            },
        )

    assert response.status_code == 201
    assert response.json()["image_url"] == "https://basket-46.wbbasket.ru/card.webp"
    wb_client_factory.assert_not_called()


def test_numeric_wb_article_outside_seller_catalog_returns_clear_error(
    client, test_account
):
    wb_client = SimpleNamespace(
        search_cards=AsyncMock(side_effect=asyncio.TimeoutError())
    )
    public_product = SimpleNamespace(name="Public WB product")

    with (
        patch("app.api.products.create_wb_client", return_value=wb_client),
        patch(
            "app.services.catalog_parser.catalog_parser.parse_product",
            new=AsyncMock(return_value=public_product),
        ),
    ):
        response = client.get(
            "/api/products/search-wb",
            params={"account_id": test_account.id, "text": "1229563360"},
        )

    assert response.status_code == 404
    assert "не найден в выбранном кабинете" in response.json()["detail"]


def test_numeric_seller_sku_uses_exact_content_card_and_returns_photo(
    client, test_account
):
    matching_card = {
        "nmID": 1394057535,
        "vendorCode": "3895399",
        "title": "Seller card",
        "photos": [{"c246x328": "https://basket.example/seller.webp"}],
    }
    wb_client = SimpleNamespace(search_cards=AsyncMock(return_value=[matching_card]))

    with patch("app.api.products.create_wb_client", return_value=wb_client):
        response = client.get(
            "/api/products/search-wb",
            params={"account_id": test_account.id, "text": "3895399"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "nm_id": 1394057535,
        "seller_sku": "3895399",
        "name": "Seller card",
        "image_url": "https://basket.example/seller.webp",
    }
