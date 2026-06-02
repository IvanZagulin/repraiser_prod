"""
Tests for leads (landing page form submissions).
"""
from app.models.lead import Lead


class TestLeadSubmit:
    def test_submit_valid(self, anon_client):
        r = anon_client.post("/api/leads", json={
            "name": "???????? ????????????", "email": "ivan@example.com", "phone": "+79001234567"})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_submit_without_phone(self, anon_client):
        r = anon_client.post("/api/leads", json={
            "name": "??????????", "email": "maria@example.com"})
        assert r.status_code == 200

    def test_invalid_email(self, anon_client):
        r = anon_client.post("/api/leads", json={
            "name": "Test", "email": "not-an-email"})
        assert r.status_code == 422

    def test_lead_saved_to_db(self, anon_client, db_session):
        before = db_session.query(Lead).count()
        anon_client.post("/api/leads", json={
            "name": "DB Test", "email": "dbtest_l@example.com"})
        after = db_session.query(Lead).count()
        assert after > before

    def test_no_auth_needed(self, anon_client):
        r = anon_client.post("/api/leads", json={
            "name": "Public", "email": "public@test.com"})
        assert r.status_code in (200, 429)


class TestLeadsAdminList:
    def test_requires_auth(self, anon_client):
        r = anon_client.get("/api/admin/leads")
        assert r.status_code in (401, 403)
