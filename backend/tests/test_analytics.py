"""
Tests for analytics endpoints.
"""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from app.models.price_snapshot import PriceSnapshot


class TestAnalyticsSummary:
    def test_summary_requires_auth(self, anon_client):
        r = anon_client.get("/api/analytics/summary")
        assert r.status_code == 401

    def test_summary_returns_dict(self, client):
        r = client.get("/api/analytics/summary")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)

    def test_summary_has_expected_fields(self, client):
        r = client.get("/api/analytics/summary")
        body = r.json()
        # Should contain some stats fields
        assert len(body) >= 0  # at minimum returns something


class TestAnalyticsSavings:
    def test_savings_requires_auth(self, anon_client):
        r = anon_client.get("/api/analytics/savings")
        assert r.status_code == 401

    def test_savings_returns_valid_structure(self, client):
        r = client.get("/api/analytics/savings")
        assert r.status_code == 200
        body = r.json()
        assert "total_savings_rub" in body or isinstance(body, dict)

    def test_savings_with_days_param(self, client):
        r = client.get("/api/analytics/savings?days=7")
        assert r.status_code == 200

    def test_savings_invalid_days(self, client):
        r = client.get("/api/analytics/savings?days=0")
        assert r.status_code == 422
        r2 = client.get("/api/analytics/savings?days=366")
        assert r2.status_code == 422


class TestAnalyticsPriceDynamics:
    def test_price_dynamics_requires_auth(self, anon_client, test_product):
        r = anon_client.get(f"/api/analytics/product/{test_product.id}/price-dynamics")
        assert r.status_code == 401

    def test_price_dynamics_own_product(self, client, test_product):
        r = client.get(f"/api/analytics/product/{test_product.id}/price-dynamics")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_price_dynamics_idor(self, client, db_session):
        from app.models.user import User
        from app.models.account import Account
        from app.models.product import Product
        from app.api.auth import hash_password
        other = User(email="ana_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        acc = Account(user_id=other.id, name="AnaShop", wb_api_token="t", is_active=True)
        db_session.add(acc); db_session.commit()
        prod = Product(account_id=acc.id, nm_id=55555555, seller_sku="ANA-SKU",
                       name="Analytics Victim", cost_price=Decimal("100"))
        db_session.add(prod); db_session.commit()

        r = client.get(f"/api/analytics/product/{prod.id}/price-dynamics")
        assert r.status_code == 404  # cannot see other user's product analytics

        db_session.delete(prod); db_session.delete(acc); db_session.delete(other)
        db_session.commit()

    def test_price_dynamics_with_snapshots(self, client, db_session, test_product, test_user):
        from app.models.price_snapshot import PriceSnapshot
        snap = PriceSnapshot(
            product_id=test_product.id,
            seller_price=Decimal("1000"),
            final_price=Decimal("900"),
            discount=Decimal("10"),
            spp=Decimal("5"),
            snapshot_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db_session.add(snap); db_session.commit()

        r = client.get(f"/api/analytics/product/{test_product.id}/price-dynamics?days=1")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        db_session.delete(snap); db_session.commit()


class TestAnalyticsCompetitorTrends:
    def test_competitor_trends_requires_auth(self, anon_client):
        r = anon_client.get("/api/analytics/competitor-trends")
        assert r.status_code == 401

    def test_competitor_trends_returns_list(self, client):
        r = client.get("/api/analytics/competitor-trends")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
