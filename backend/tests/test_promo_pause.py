import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import ConfigChangeLog, PromoPauseRegistry
from app.services.promo_pause import sync_promo_pauses


class _FakeWBClient:
    def __init__(self, prices):
        self.prices = prices

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get_prices_by_articles(self, _nm_ids):
        return self.prices


def _price(nm_id, discount):
    return SimpleNamespace(
        nm_id=nm_id,
        discount=Decimal(str(discount)),
        seller_price=Decimal("1000"),
        discounted_price=Decimal("1000") * (Decimal("1") - Decimal(str(discount)) / 100),
    )


def _sync(db_session, test_user, prices):
    with patch("app.services.promo_pause.is_token_expired", return_value=False), \
         patch(
             "app.services.promo_pause.create_wb_client",
             side_effect=lambda _token: _FakeWBClient(prices),
         ):
        return asyncio.run(sync_promo_pauses(db_session, test_user.id))


def test_discount_pauses_and_two_zero_checks_resume(
    db_session, test_user, test_product, test_product_config,
):
    first = _sync(db_session, test_user, [_price(test_product.nm_id, 10)])
    db_session.refresh(test_product_config)
    registry = db_session.query(PromoPauseRegistry).one()

    assert first.checked == 1
    assert first.paused_product_ids == [test_product.id]
    assert test_product_config.is_active is False
    assert registry.is_paused is True
    assert registry.discount_pct == Decimal("10.00")

    second = _sync(db_session, test_user, [_price(test_product.nm_id, 0)])
    db_session.refresh(test_product_config)
    db_session.refresh(registry)
    assert second.resumed_product_ids == []
    assert registry.zero_streak == 1
    assert registry.is_paused is True
    assert test_product_config.is_active is False

    third = _sync(db_session, test_user, [_price(test_product.nm_id, 0)])
    db_session.refresh(test_product_config)
    db_session.refresh(registry)
    assert third.resumed_product_ids == [test_product.id]
    assert registry.zero_streak == 2
    assert registry.is_paused is False
    assert test_product_config.is_active is True

    changes = db_session.query(ConfigChangeLog).filter(
        ConfigChangeLog.product_id == test_product.id,
        ConfigChangeLog.field_name == "promo_auto_pause",
    ).all()
    assert [(row.old_value, row.new_value) for row in changes] == [
        ("active", "discount=10%"),
        ("discount", "active"),
    ]


def test_incomplete_prices_response_rolls_back_without_pausing(
    db_session, test_user, test_product_config,
):
    with pytest.raises(RuntimeError, match="не вернул"):
        _sync(db_session, test_user, [])

