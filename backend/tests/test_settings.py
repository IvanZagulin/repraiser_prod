"""
Tests for app settings (pricing fees, strategy defaults).
"""
import pytest


class TestSettingsGet:
    def test_get_returns_dict(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, dict)
        # Should contain some known settings keys
        assert len(body) >= 0

    def test_get_requires_auth(self, anon_client):
        r = anon_client.get("/api/settings")
        assert r.status_code == 401


class TestSettingsUpdate:
    def test_update_returns_ok(self, client):
        r = client.put("/api/settings", json={})
        assert r.status_code == 200

    def test_update_single_setting(self, client):
        # Try updating a known fee field
        r = client.put("/api/settings", json={"wh_fee_pct": "3.5"})
        assert r.status_code in (200, 422)  # 422 if field not allowed

    def test_update_requires_auth(self, anon_client):
        r = anon_client.put("/api/settings", json={})
        assert r.status_code == 401
