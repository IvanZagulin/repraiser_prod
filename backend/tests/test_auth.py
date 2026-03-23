"""
Tests for authentication endpoints and JWT lifecycle.

Covers:
  - POST /api/auth/register  (success, duplicate email)
  - POST /api/auth/login     (success, wrong password, inactive, unverified)
  - GET  /api/auth/verify    (valid token, expired, wrong token_version)
  - POST /api/auth/logout    (increments token_version)
  - httpOnly cookie is set on login
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from jose import jwt

from app.api.auth import (
    create_access_token,
    hash_password,
    verify_password,
    generate_code,
    _COOKIE_NAME,
)
from app.config import settings
from app.models.user import User


# ─── Helper unit tests ───────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self):
        plain = "SecureP@ssw0rd!"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("CorrectPassword1")
        assert verify_password("WrongPassword99", hashed) is False


class TestGenerateCode:
    def test_code_is_6_digits(self):
        code = generate_code()
        assert len(code) == 6
        assert code.isdigit()

    def test_codes_are_different(self):
        codes = {generate_code() for _ in range(20)}
        # With 10^6 possibilities, 20 codes should be unique
        assert len(codes) == 20


class TestCreateAccessToken:
    def test_token_contains_expected_claims(self, test_user):
        token = create_access_token(test_user)
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        assert payload["sub"] == test_user.email
        assert payload["user_id"] == test_user.id
        assert payload["is_admin"] == test_user.is_admin
        assert payload["token_version"] == test_user.token_version
        assert "exp" in payload

    def test_token_expires_in_future(self, test_user):
        token = create_access_token(test_user)
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        assert payload["exp"] > int(time.time())


# ─── POST /api/auth/register ─────────────────────────────────────────────────

class TestRegister:
    @patch("app.services.email_service.send_verification_code")
    @patch("app.services.email_service.send_welcome_email")
    def test_register_success(self, mock_welcome, mock_send_code, anon_client, db_session):
        resp = anon_client.post("/api/auth/register", json={
            "email": "newuser@example.com",
            "password": "StrongPass123",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert "message" in body

        # User created in DB
        user = db_session.query(User).filter(User.email == "newuser@example.com").first()
        assert user is not None
        assert user.is_verified is False
        assert user.verification_code is not None
        mock_send_code.assert_called_once()

    @patch("app.services.email_service.send_verification_code")
    @patch("app.services.email_service.send_welcome_email")
    def test_register_duplicate_email(self, mock_welcome, mock_send_code, anon_client, test_user):
        resp = anon_client.post("/api/auth/register", json={
            "email": test_user.email,
            "password": "AnotherPass123",
        })
        assert resp.status_code == 409
        assert "уже существует" in resp.json()["detail"]

    def test_register_weak_password_rejected(self, anon_client):
        resp = anon_client.post("/api/auth/register", json={
            "email": "weak@example.com",
            "password": "short",  # min_length=8 in schema
        })
        assert resp.status_code == 422  # pydantic validation error


# ─── POST /api/auth/login ────────────────────────────────────────────────────

class TestLogin:
    def test_login_success(self, anon_client, test_user):
        resp = anon_client.post("/api/auth/login", json={
            "email": test_user.email,
            "password": "TestPassword123",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == settings.JWT_EXPIRE_HOURS * 3600

    def test_login_sets_httponly_cookie(self, anon_client, test_user):
        resp = anon_client.post("/api/auth/login", json={
            "email": test_user.email,
            "password": "TestPassword123",
        })
        assert resp.status_code == 200
        # TestClient stores cookies; check the raw Set-Cookie header
        cookie_header = resp.headers.get("set-cookie", "")
        assert _COOKIE_NAME in cookie_header
        assert "httponly" in cookie_header.lower()

    def test_login_wrong_password(self, anon_client, test_user):
        resp = anon_client.post("/api/auth/login", json={
            "email": test_user.email,
            "password": "TotallyWrong999",
        })
        assert resp.status_code == 401

    def test_login_nonexistent_email(self, anon_client):
        resp = anon_client.post("/api/auth/login", json={
            "email": "nobody@example.com",
            "password": "NoMatter12345",
        })
        assert resp.status_code == 401

    def test_login_inactive_user(self, anon_client, inactive_user):
        resp = anon_client.post("/api/auth/login", json={
            "email": inactive_user.email,
            "password": "BlockedPass123",
        })
        assert resp.status_code == 403
        assert "заблокирован" in resp.json()["detail"].lower()

    def test_login_unverified_user(self, anon_client, unverified_user):
        resp = anon_client.post("/api/auth/login", json={
            "email": unverified_user.email,
            "password": "UnverifiedPass123",
        })
        assert resp.status_code == 403
        assert "не подтверждён" in resp.json()["detail"].lower()


# ─── GET /api/auth/verify ────────────────────────────────────────────────────

class TestVerifyToken:
    def test_verify_valid_token(self, client, test_user):
        """Authenticated client should get user info back."""
        resp = client.get("/api/auth/verify")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == test_user.id
        assert body["email"] == test_user.email

    def test_verify_no_token_returns_401(self, anon_client):
        resp = anon_client.get("/api/auth/verify")
        assert resp.status_code == 401

    def test_verify_expired_token_returns_401(self, anon_client, test_user):
        """Craft a token that expired in the past."""
        payload = {
            "sub": test_user.email,
            "user_id": test_user.id,
            "is_admin": False,
            "token_version": test_user.token_version,
            "exp": int(time.time()) - 3600,  # expired 1h ago
        }
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        resp = anon_client.get(
            "/api/auth/verify",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    def test_verify_wrong_token_version_returns_401(self, anon_client, test_user, db_session):
        """Token issued before password change (old token_version) should be rejected."""
        old_token = create_access_token(test_user)

        # Simulate password change — bump token_version
        test_user.token_version = (test_user.token_version or 1) + 1
        db_session.commit()

        resp = anon_client.get(
            "/api/auth/verify",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        assert resp.status_code == 401


# ─── POST /api/auth/logout ───────────────────────────────────────────────────

class TestLogout:
    def test_logout_increments_token_version(self, client, test_user, db_session):
        old_version = test_user.token_version or 1
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200

        db_session.refresh(test_user)
        assert test_user.token_version == old_version + 1

    def test_logout_clears_cookie(self, client):
        resp = client.post("/api/auth/logout")
        cookie_header = resp.headers.get("set-cookie", "")
        # Cookie should be deleted (max-age=0 or expires in the past)
        assert _COOKIE_NAME in cookie_header
