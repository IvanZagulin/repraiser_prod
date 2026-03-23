"""
API endpoint smoke tests.

Uses the authenticated `client` fixture from conftest (overrides get_current_user).
Verifies basic CRUD patterns and response shapes without hitting external services.
"""

from datetime import datetime
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.product import Product
from app.models.product_config import ProductConfig
from app.models.repricer_run import RepricerRun
from app.models.app_setting import AppSetting


# ═══════════════════════════════════════════════════════════════════════════════
# GET / — root
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoot:
    def test_root_returns_ok(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/accounts
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccountsList:
    def test_empty_list(self, client):
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["accounts"] == []

    def test_returns_only_own_accounts(self, client, test_account, db_session, test_user):
        """test_user should see test_account but NOT another user's account."""
        # Create another user's account
        from app.models.user import User
        from app.api.auth import hash_password
        other_user = User(
            email="other@example.com",
            password_hash=hash_password("OtherPass1234"),
            is_active=True, is_admin=False, is_verified=True,
            token_version=1, verification_attempts=0, reset_attempts=0,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        other_account = Account(
            user_id=other_user.id,
            name="Other Shop",
            wb_api_token="other-fake-token",
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(other_account)
        db_session.commit()

        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["accounts"][0]["name"] == test_account.name

    def test_pagination_params(self, client, test_account):
        resp = client.get("/api/accounts?skip=0&limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["accounts"]) <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/products
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductsList:
    def test_empty_products(self, client):
        resp = client.get("/api/products")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0

    def test_products_with_data(self, client, test_product, test_product_config):
        resp = client.get("/api/products")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        product = body["products"][0]
        assert product["nm_id"] == test_product.nm_id
        assert product["seller_sku"] == test_product.seller_sku

    def test_products_pagination(self, client, test_product, test_product_config):
        resp = client.get("/api/products?page=1&page_size=10")
        assert resp.status_code == 200
        body = resp.json()
        assert body["page"] == 1
        assert body["page_size"] == 10

    def test_products_filter_by_account(self, client, test_product, test_account, test_product_config):
        resp = client.get(f"/api/products?account_id={test_account.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1

    def test_products_filter_by_nonexistent_account(self, client):
        resp = client.get("/api/products?account_id=99999")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0

    def test_products_strategy_filter(self, client, test_product, test_product_config):
        resp = client.get("/api/products?strategy=rrp")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1

        resp2 = client.get("/api/products?strategy=competitor")
        assert resp2.status_code == 200
        body2 = resp2.json()
        # test_product_config uses rrp, so competitor filter should exclude it
        assert body2["total"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/auth/verify (authenticated)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthVerifyViaClient:
    def test_verify_returns_user_info_and_usage(self, client, test_user, test_account, test_product):
        resp = client.get("/api/auth/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == test_user.id
        assert body["email"] == test_user.email
        assert "usage" in body
        assert body["usage"]["accounts"] >= 1
        assert body["usage"]["products"] >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Repricer runs list
# ═══════════════════════════════════════════════════════════════════════════════

class TestRepricerRuns:
    def test_list_runs_empty(self, client):
        resp = client.get("/api/repricer/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0

    def test_list_runs_with_data(self, client, db_session, test_user):
        run = RepricerRun(
            user_id=test_user.id,
            run_type="manual",
            status="completed",
            total_products=5,
            updated_count=3,
            skipped_count=1,
            error_count=1,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        db_session.add(run)
        db_session.commit()

        resp = client.get("/api/repricer/runs")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        first_run = body["runs"][0]
        assert first_run["run_type"] == "manual"
        assert first_run["status"] == "completed"

    def test_runs_isolated_by_user(self, client, db_session, test_user):
        """Runs from other users should not appear."""
        from app.models.user import User
        from app.api.auth import hash_password
        other_user = User(
            email="other_runs@example.com",
            password_hash=hash_password("OtherPass1234"),
            is_active=True, is_admin=False, is_verified=True,
            token_version=1, verification_attempts=0, reset_attempts=0,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        other_run = RepricerRun(
            user_id=other_user.id,
            run_type="scheduled",
            status="completed",
            total_products=10,
            updated_count=10,
            skipped_count=0,
            error_count=0,
            started_at=datetime.utcnow(),
        )
        db_session.add(other_run)
        db_session.commit()

        resp = client.get("/api/repricer/runs")
        assert resp.status_code == 200
        body = resp.json()
        # Should not include the other user's run
        for run in body.get("runs", []):
            assert run.get("user_id", test_user.id) == test_user.id


# ═══════════════════════════════════════════════════════════════════════════════
# Security: unauthenticated access
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnauthenticatedAccess:
    def test_accounts_requires_auth(self, anon_client):
        resp = anon_client.get("/api/accounts")
        assert resp.status_code == 401

    def test_products_requires_auth(self, anon_client):
        resp = anon_client.get("/api/products")
        assert resp.status_code == 401

    def test_repricer_runs_requires_auth(self, anon_client):
        resp = anon_client.get("/api/repricer/runs")
        assert resp.status_code == 401
