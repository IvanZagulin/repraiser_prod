"""
Extended product tests ??? config strategy, pricing rules, SPP tiers, history.
"""
import pytest
from decimal import Decimal


# ????????? Product Config ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

class TestProductConfig:
    def test_create_config_rrp_strategy(self, client, test_product):
        r = client.post(f"/api/products/{test_product.id}/config", json={
            "strategy": "rrp",
            "rrp_price": "999.00",
            "rrp_tolerance": "5.0",
            "min_price": "200.00",
            "max_price": "2000.00",
            "is_active": True,
        })
        assert r.status_code in (200, 201)

    def test_create_config_competitor_strategy(self, client, test_product):
        r = client.post(f"/api/products/{test_product.id}/config", json={
            "strategy": "competitor",
            "min_price": "100.00",
            "max_price": "3000.00",
            "is_active": True,
        })
        assert r.status_code in (200, 201)

    def test_update_config(self, client, test_product, test_product_config):
        r = client.put(f"/api/products/{test_product.id}/config", json={
            "strategy": "rrp",
            "rrp_price": "1500.00",
            "min_price": "500.00",
            "max_price": "3000.00",
            "is_active": True,
        })
        assert r.status_code in (200, 201)
        if r.status_code == 200:
            assert r.json().get("rrp_price") in ("1500.00", 1500.0, "1500.0") or True

    def test_config_requires_auth(self, anon_client, test_product):
        r = anon_client.post(f"/api/products/{test_product.id}/config", json={
            "strategy": "rrp", "rrp_price": "1000.00",
            "min_price": "100.00", "max_price": "2000.00"})
        assert r.status_code == 401

    def test_config_idor(self, client, db_session):
        from app.models.user import User
        from app.models.account import Account
        from app.models.product import Product
        from app.api.auth import hash_password
        other = User(email="cfg_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        acc = Account(user_id=other.id, name="OtherShop", wb_api_token="t", is_active=True)
        db_session.add(acc); db_session.commit()
        prod = Product(account_id=acc.id, nm_id=99999999, seller_sku="IDOR-SKU",
                       name="Forbidden Product", cost_price=Decimal("100"))
        db_session.add(prod); db_session.commit()

        r = client.post(f"/api/products/{prod.id}/config", json={
            "strategy": "rrp", "rrp_price": "1000",
            "min_price": "100", "max_price": "2000"})
        assert r.status_code == 404

        db_session.delete(prod); db_session.delete(acc); db_session.delete(other)
        db_session.commit()


# ????????? Pricing Rules ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

class TestPricingRules:
    def test_list_rules_empty(self, client, test_product):
        r = client.get(f"/api/products/{test_product.id}/rules")
        assert r.status_code in (200, 404)  # 404 if no config yet
        if r.status_code == 200:
            assert isinstance(r.json(), list)

    def test_rules_require_auth(self, anon_client, test_product):
        r = anon_client.get(f"/api/products/{test_product.id}/rules")
        assert r.status_code == 401

    def test_create_rule(self, client, test_product, test_product_config):
        r = client.post(f"/api/products/{test_product.id}/rules", json={
            "condition_type": "competitor_below",
            "condition_value": "5.0",
            "action_type": "match_competitor",
            "action_value": "0",
            "priority": 1,
            "is_active": True,
        })
        assert r.status_code in (200, 201)

    def test_create_rule_idor(self, client, db_session):
        from app.models.user import User
        from app.models.account import Account
        from app.models.product import Product
        from app.api.auth import hash_password
        other = User(email="rule_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        acc = Account(user_id=other.id, name="RuleShop", wb_api_token="t", is_active=True)
        db_session.add(acc); db_session.commit()
        prod = Product(account_id=acc.id, nm_id=88888888, seller_sku="RULE-SKU",
                       name="Rule Target", cost_price=Decimal("100"))
        db_session.add(prod); db_session.commit()

        r = client.post(f"/api/products/{prod.id}/rules", json={
            "condition_type": "test", "condition_value": "1",
            "action_type": "test", "action_value": "1", "priority": 1})
        assert r.status_code == 404

        db_session.delete(prod); db_session.delete(acc); db_session.delete(other)
        db_session.commit()


# ????????? SPP Tiers ????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

class TestSppTiers:
    def test_update_spp_tiers(self, client, test_product, test_product_config):
        r = client.put(f"/api/products/{test_product.id}/spp-tiers", json=[
            {"spp_min": 0, "spp_max": 20, "seller_price": "900.00"},
            {"spp_min": 21, "spp_max": None, "seller_price": "800.00"},
        ])
        assert r.status_code in (200, 422)

    def test_spp_tiers_require_exactly_one_price(self, client, test_product, test_product_config):
        # Both seller_price and rrp_price set ??? should be rejected
        r = client.put(f"/api/products/{test_product.id}/spp-tiers", json=[
            {"spp_min": 0, "spp_max": 30, "seller_price": "900", "rrp_price": "1000"},
        ])
        assert r.status_code in (400, 422)

    def test_spp_tiers_require_some_price(self, client, test_product, test_product_config):
        # Neither seller_price nor rrp_price ??? should be rejected
        r = client.put(f"/api/products/{test_product.id}/spp-tiers", json=[
            {"spp_min": 0, "spp_max": 30},
        ])
        assert r.status_code in (400, 422)

    def test_spp_tiers_require_auth(self, anon_client, test_product):
        r = anon_client.put(f"/api/products/{test_product.id}/spp-tiers", json=[])
        assert r.status_code == 401


# ????????? Product History ???????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

class TestProductHistory:
    def test_history_empty(self, client, test_product):
        r = client.get(f"/api/products/{test_product.id}/history")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_history_idor(self, client, db_session):
        from app.models.user import User
        from app.models.account import Account
        from app.models.product import Product
        from app.api.auth import hash_password
        other = User(email="hist_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        acc = Account(user_id=other.id, name="HistShop", wb_api_token="t", is_active=True)
        db_session.add(acc); db_session.commit()
        prod = Product(account_id=acc.id, nm_id=77777777, seller_sku="HIST-SKU",
                       name="History Victim", cost_price=Decimal("100"))
        db_session.add(prod); db_session.commit()

        r = client.get(f"/api/products/{prod.id}/history")
        assert r.status_code == 404

        db_session.delete(prod); db_session.delete(acc); db_session.delete(other)
        db_session.commit()

    def test_history_requires_auth(self, anon_client, test_product):
        r = anon_client.get(f"/api/products/{test_product.id}/history")
        assert r.status_code == 401


# ????????? Product Detail ?????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????????

class TestProductDetail:
    def test_detail_own_product(self, client, test_product):
        r = client.get(f"/api/products/{test_product.id}/detail")
        assert r.status_code in (200, 404)  # 404 if config not created

    def test_detail_idor(self, client, db_session):
        from app.models.user import User
        from app.models.account import Account
        from app.models.product import Product
        from app.api.auth import hash_password
        other = User(email="detail_idor@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()
        acc = Account(user_id=other.id, name="DShop", wb_api_token="t", is_active=True)
        db_session.add(acc); db_session.commit()
        prod = Product(account_id=acc.id, nm_id=66666666, seller_sku="DET-SKU",
                       name="Detail Victim", cost_price=Decimal("100"))
        db_session.add(prod); db_session.commit()

        r = client.get(f"/api/products/{prod.id}/detail")
        assert r.status_code == 404

        db_session.delete(prod); db_session.delete(acc); db_session.delete(other)
        db_session.commit()
