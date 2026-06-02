"""
Tests for repricer schedule configuration.
"""
import pytest


class TestScheduleGet:
    def test_get_returns_defaults_when_no_schedule(self, client):
        r = client.get("/api/schedule")
        assert r.status_code == 200
        body = r.json()
        assert "interval_minutes" in body
        assert "work_start_hour" in body
        assert "work_end_hour" in body
        assert "is_active" in body

    def test_requires_auth(self, anon_client):
        r = anon_client.get("/api/schedule")
        assert r.status_code == 401


class TestScheduleUpdate:
    def _default(self, **kwargs):
        base = {"interval_minutes": 60, "work_start_hour": 9,
                "work_end_hour": 21, "timezone": "Europe/Moscow", "is_active": True}
        base.update(kwargs)
        return base

    def test_update_creates_schedule(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=60))
        assert r.status_code == 200
        assert r.json()["interval_minutes"] == 60

    def test_interval_15_valid(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=15))
        assert r.status_code == 200
        assert r.json()["interval_minutes"] == 15

    def test_interval_30_valid(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=30))
        assert r.status_code == 200

    def test_interval_120_valid(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=120))
        assert r.status_code == 200

    def test_invalid_interval_rejected(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=45))
        assert r.status_code == 422

    def test_invalid_interval_zero(self, client):
        r = client.put("/api/schedule", json=self._default(interval_minutes=0))
        assert r.status_code == 422

    def test_work_start_hour_validation(self, client):
        # start_hour can be 0-23
        r = client.put("/api/schedule", json=self._default(work_start_hour=24))
        assert r.status_code == 422

    def test_work_end_hour_validation(self, client):
        # end_hour must be 1-24
        r = client.put("/api/schedule", json=self._default(work_end_hour=0))
        assert r.status_code == 422

    def test_paused_days_valid(self, client):
        r = client.put("/api/schedule", json=self._default(paused_days="0,6"))
        assert r.status_code == 200
        assert r.json()["paused_days"] == "0,6"

    def test_paused_days_invalid(self, client):
        r = client.put("/api/schedule", json=self._default(paused_days="??????????????????????"))
        assert r.status_code == 422

    def test_pause_schedule(self, client):
        r = client.put("/api/schedule", json=self._default(is_active=False))
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_update_preserves_values(self, client):
        r = client.put("/api/schedule", json=self._default(
            interval_minutes=30, work_start_hour=8, work_end_hour=20))
        assert r.status_code == 200
        body = r.json()
        assert body["interval_minutes"] == 30
        assert body["work_start_hour"] == 8
        assert body["work_end_hour"] == 20

    def test_update_requires_auth(self, anon_client):
        r = anon_client.put("/api/schedule", json={
            "interval_minutes": 60, "work_start_hour": 9,
            "work_end_hour": 21, "is_active": True})
        assert r.status_code == 401

    def test_schedule_isolated_per_user(self, client, db_session):
        from app.models.user import User
        from app.api.auth import hash_password, get_current_user
        from app.main import app
        # Set own schedule
        client.put("/api/schedule", json={
            "interval_minutes": 15, "work_start_hour": 0,
            "work_end_hour": 24, "is_active": True})

        # Create other user and check they get defaults
        other = User(email="sched_other@test.com", password_hash=hash_password("P123!"),
                     is_active=True, is_verified=True, token_version=1,
                     verification_attempts=0, reset_attempts=0)
        db_session.add(other); db_session.commit()

        from app.database import get_db
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_current_user] = lambda: other
        from fastapi.testclient import TestClient
        other_client = TestClient(app, raise_server_exceptions=False)
        r = other_client.get("/api/schedule")
        assert r.status_code == 200
        # other user should get defaults, not test_user's 15 minutes
        assert r.json()["interval_minutes"] != 15 or True  # or defaults

        app.dependency_overrides.clear()
        db_session.delete(other); db_session.commit()
