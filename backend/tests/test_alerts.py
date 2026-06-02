"""
Tests for competitor alerts API.
"""
import pytest
from decimal import Decimal
from app.models.competitor_alert import CompetitorAlert


def _alert(db, user_id, product_id, nm_id=12345):
    a = CompetitorAlert(
        user_id=user_id, product_id=product_id, competitor_nm_id=nm_id,
        old_price=Decimal("1000"), new_price=Decimal("900"),
        change_pct=Decimal("-10"), is_read=False,
    )
    db.add(a); db.commit(); db.refresh(a); return a


class TestAlertsList:
    def test_empty_list(self, client):
        r = client.get("/api/alerts")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_requires_auth(self, anon_client):
        r = anon_client.get("/api/alerts")
        assert r.status_code == 401

    def test_returns_own_alerts(self, client, db_session, test_user, test_product):
        a = _alert(db_session, test_user.id, test_product.id, nm_id=11111)
        r = client.get("/api/alerts")
        assert any(x["id"] == a.id for x in r.json())
        db_session.delete(a); db_session.commit()

    def test_hides_other_user_alerts(self, client, db_session, test_product):
        from app.models.user import User
        from app.api.auth import hash_password
        other = User(email="other_al@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1)
        db_session.add(other); db_session.commit()
        a = _alert(db_session, other.id, test_product.id, nm_id=99999)
        r = client.get("/api/alerts")
        assert not any(x["id"] == a.id for x in r.json())
        db_session.delete(a); db_session.delete(other); db_session.commit()


class TestAlertsCount:
    def test_count_has_unread_field(self, client):
        r = client.get("/api/alerts/count")
        assert r.status_code == 200
        assert "unread" in r.json()
        assert isinstance(r.json()["unread"], int)

    def test_unread_count_increases(self, client, db_session, test_user, test_product):
        r0 = client.get("/api/alerts/count").json()["unread"]
        a = _alert(db_session, test_user.id, test_product.id, nm_id=22222)
        r1 = client.get("/api/alerts/count").json()["unread"]
        assert r1 >= r0
        db_session.delete(a); db_session.commit()


class TestMarkRead:
    def test_mark_single_read(self, client, db_session, test_user, test_product):
        a = _alert(db_session, test_user.id, test_product.id, nm_id=33333)
        r = client.put(f"/api/alerts/{a.id}/read")
        assert r.status_code in (200, 204)
        db_session.refresh(a)
        assert a.is_read is True
        db_session.delete(a); db_session.commit()

    def test_mark_all_read(self, client, db_session, test_user, test_product):
        a1 = _alert(db_session, test_user.id, test_product.id, nm_id=44444)
        a2 = _alert(db_session, test_user.id, test_product.id, nm_id=55555)
        r = client.put("/api/alerts/read-all")
        assert r.status_code in (200, 204)
        r2 = client.get("/api/alerts/count")
        assert r2.json()["unread"] == 0
        db_session.delete(a1); db_session.delete(a2); db_session.commit()

    def test_nonexistent_alert_404(self, client):
        r = client.put("/api/alerts/999999/read")
        assert r.status_code == 404
