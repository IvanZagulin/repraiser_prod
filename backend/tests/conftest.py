"""
Shared pytest fixtures for the WB Repricer backend test suite.

Uses SQLite in-memory DB so tests run fast without PostgreSQL.
Overrides FastAPI dependencies (get_db, get_current_user) globally.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Environment: set env vars BEFORE any app code is imported so that
# pydantic Settings picks them up via env_file / direct env vars.
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-pytest-only")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_EXPIRE_HOURS", "24")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("FERNET_KEY", "")
os.environ.setdefault("ADMIN_EMAIL", "")
os.environ.setdefault("ADMIN_PASSWORD", "")

# Ensure backend package is importable
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database import Base, get_db
from app.models.user import User
from app.models.account import Account
from app.models.product import Product
from app.models.product_config import ProductConfig
from app.models.repricer_run import RepricerRun
from app.models.price_change import PriceChange
from app.models.app_setting import AppSetting
from app.models.plan import Plan
from app.api.auth import get_current_user, hash_password, create_access_token

# ---------------------------------------------------------------------------
# SQLite in-memory engine (with FK enforcement)
# ---------------------------------------------------------------------------
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
)

# Enable foreign key enforcement on every connection (SQLite default is OFF)
@event.listens_for(TEST_ENGINE, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Create all tables once at the start of the test session."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture()
def db_session():
    """
    Yield a clean DB session that rolls back after each test.
    Uses nested transactions so tests are fully isolated.
    """
    connection = TEST_ENGINE.connect()
    transaction = connection.begin()
    session = TestSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def test_user(db_session):
    """Create and return a verified, active test user."""
    user = User(
        email="test@example.com",
        password_hash=hash_password("TestPassword123"),
        is_active=True,
        is_admin=False,
        is_verified=True,
        token_version=1,
        onboarding_completed=False,
        trial_started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        trial_ends_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
        verification_attempts=0,
        reset_attempts=0,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def admin_user(db_session):
    """Create and return an admin user."""
    user = User(
        email="admin@example.com",
        password_hash=hash_password("AdminPassword123"),
        is_active=True,
        is_admin=True,
        is_verified=True,
        token_version=1,
        onboarding_completed=True,
        verification_attempts=0,
        reset_attempts=0,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def inactive_user(db_session):
    """Create and return an inactive (blocked) user."""
    user = User(
        email="blocked@example.com",
        password_hash=hash_password("BlockedPass123"),
        is_active=False,
        is_admin=False,
        is_verified=True,
        token_version=1,
        verification_attempts=0,
        reset_attempts=0,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def unverified_user(db_session):
    """Create and return an unverified user."""
    user = User(
        email="unverified@example.com",
        password_hash=hash_password("UnverifiedPass123"),
        is_active=True,
        is_admin=False,
        is_verified=False,
        token_version=1,
        verification_attempts=0,
        reset_attempts=0,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def test_account(db_session, test_user):
    """Create and return a WB seller account linked to test_user."""
    account = Account(
        user_id=test_user.id,
        name="Test WB Shop",
        # Plain text token (no Fernet in test mode)
        wb_api_token="fake-wb-token-for-testing",
        is_active=True,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


@pytest.fixture()
def test_product(db_session, test_account):
    """Create and return a product under test_account."""
    product = Product(
        account_id=test_account.id,
        nm_id=431871218,
        seller_sku="SKU-001",
        name="Test Product Widget",
        cost_price=Decimal("500.00"),
        created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)
    return product


@pytest.fixture()
def test_product_config(db_session, test_product):
    """Create and return a product config (RRP strategy) for test_product."""
    config = ProductConfig(
        product_id=test_product.id,
        strategy="rrp",
        is_active=True,
        min_price=Decimal("300.00"),
        max_price=Decimal("2000.00"),
        rrp_price=Decimal("1000.00"),
        rrp_tolerance=Decimal("10.00"),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)
    return config


@pytest.fixture()
def auth_token(test_user):
    """Return a valid JWT for test_user."""
    return create_access_token(test_user)


@pytest.fixture()
def client(db_session, test_user):
    """
    FastAPI TestClient with overridden DB and auth dependencies.
    All requests are authenticated as test_user by default.
    """
    # Import app lazily to avoid import-time side effects (Sentry, Alembic, etc.)
    # We patch the startup event away so it doesn't try to hit PostgreSQL.
    from app.main import app

    # Remove startup/shutdown event handlers that use pg_advisory_lock / Alembic
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    def _override_get_db():
        yield db_session

    def _override_get_current_user():
        return test_user

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client(db_session):
    """
    FastAPI TestClient WITHOUT auth override — for testing login/register flows.
    The DB dependency is still overridden to use the test SQLite session.
    """
    from app.main import app

    # Remove startup/shutdown event handlers that use pg_advisory_lock / Alembic
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    app.dependency_overrides.clear()
