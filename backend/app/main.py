import re
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import settings
from app.utils import get_real_ip
from app.api import auth, accounts, products, repricer, export, settings as settings_api


# ─── Sensitive Data Filter — маскирует JWT и зашифрованные токены в логах ─────

class SensitiveDataFilter(logging.Filter):
    """Маскирует JWT-токены (eyJ...) и Fernet-значения (ENC:...) в лог-сообщениях."""
    _JWT = re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+')
    _ENC = re.compile(r'ENC:[A-Za-z0-9+/=_\-]{10,}')
    _BEARER = re.compile(r'(Bearer\s+)eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+')

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._BEARER.sub(r'\1[TOKEN]', record.msg)
            record.msg = self._JWT.sub('[TOKEN]', record.msg)
            record.msg = self._ENC.sub('[ENC_TOKEN]', record.msg)
        # Маскируем args тоже (если используется % форматирование)
        if record.args:
            def _mask(v):
                if isinstance(v, str):
                    v = self._BEARER.sub(r'\1[TOKEN]', v)
                    v = self._JWT.sub('[TOKEN]', v)
                    v = self._ENC.sub('[ENC_TOKEN]', v)
                return v
            if isinstance(record.args, dict):
                record.args = {k: _mask(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_mask(v) for v in record.args)
        return True


# Применяем ко всем логгерам приложения
_sensitive_filter = SensitiveDataFilter()
logging.getLogger().addFilter(_sensitive_filter)
for _logger_name in ("app", "uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
    logging.getLogger(_logger_name).addFilter(_sensitive_filter)


# ─── Sentry before_send — не отправляем чувствительные данные ─────────────────

def _sentry_before_send(event: dict, hint: dict) -> dict:
    """Удаляет Authorization-заголовки и токены из Sentry-событий перед отправкой."""
    # Очищаем заголовки запроса
    try:
        headers = event.get("request", {}).get("headers", {})
        if "Authorization" in headers:
            headers["Authorization"] = "[Filtered]"
        if "authorization" in headers:
            headers["authorization"] = "[Filtered]"
    except Exception:
        pass

    # Маскируем строки в extra/breadcrumbs
    _jwt_re = re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+')
    _enc_re = re.compile(r'ENC:[A-Za-z0-9+/=_\-]{10,}')

    def _clean(obj):
        if isinstance(obj, str):
            obj = _jwt_re.sub('[TOKEN]', obj)
            obj = _enc_re.sub('[ENC_TOKEN]', obj)
            return obj
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(i) for i in obj]
        return obj

    try:
        if "extra" in event:
            event["extra"] = _clean(event["extra"])
        if "breadcrumbs" in event:
            event["breadcrumbs"] = _clean(event["breadcrumbs"])
        if "exception" in event:
            # Очищаем значения переменных в stack frames
            for exc in event["exception"].get("values", []):
                for frame in exc.get("stacktrace", {}).get("frames", []):
                    if "vars" in frame:
                        frame["vars"] = _clean(frame["vars"])
    except Exception:
        pass

    return event


# Sentry — инициализируем до создания app
if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,   # 10% транзакций для performance monitoring
        environment="production",
        before_send=_sentry_before_send,
    )

# Rate limiter — ключ по реальному IP клиента (с учётом Nginx X-Forwarded-For)
limiter = Limiter(key_func=get_real_ip)

app = FastAPI(
    title="WB Repricer API",
    description="SaaS система автоматического управления ценами на Wildberries",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# CORS middleware
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
    "https://qvaz.quasar-x.ru",
    "http://qvaz.quasar-x.ru",
]

# Rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)


@app.get("/")
async def root():
    return {"status": "ok", "service": "WB Repricer API", "version": "2.0.0"}


@app.get("/api/health")
async def health_check():
    """Публичный health-check — не возвращает внутренние данные инфраструктуры."""
    return {"status": "healthy"}


# Подключаем роутеры
app.include_router(auth.router,         prefix="/api/auth",     tags=["Authentication"])
app.include_router(accounts.router,     prefix="/api/accounts", tags=["Accounts"])
app.include_router(products.router,     prefix="/api/products", tags=["Products"])
app.include_router(repricer.router,     prefix="/api/repricer", tags=["Repricer"])
app.include_router(export.router,       prefix="/api/export",   tags=["Import/Export"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"])


@app.on_event("startup")
async def startup_db_migration():
    """Idempotent DB migrations + admin seed.

    Используем pg_advisory_lock(42) чтобы при --workers 2
    только один процесс выполнял ALTER TABLE одновременно —
    иначе возникает DeadlockDetected между воркерами.
    """
    from app.database import engine
    from sqlalchemy import text
    from passlib.context import CryptContext
    import logging

    log = logging.getLogger(__name__)
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    with engine.connect() as conn:
        # Блокируем до завершения миграции другим воркером (если он уже бежит)
        conn.execute(text("SELECT pg_advisory_lock(42)"))
        try:
            # 1. users table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)"
            ))

            # 1b. Email verification & password reset columns
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "is_verified BOOLEAN NOT NULL DEFAULT TRUE"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "verification_code VARCHAR(6)"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "verification_code_expires_at TIMESTAMP"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "reset_code VARCHAR(6)"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "reset_code_expires_at TIMESTAMP"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "verification_attempts SMALLINT NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "reset_attempts SMALLINT NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "token_version SMALLINT NOT NULL DEFAULT 1"
            ))

            # 2. accounts.user_id FK
            conn.execute(text(
                "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
                "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_accounts_user_id ON accounts(user_id)"
            ))

            # 3. app_settings.user_id + new unique constraint
            conn.execute(text(
                "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS "
                "user_id INTEGER REFERENCES users(id) ON DELETE CASCADE"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_app_settings_user_id ON app_settings(user_id)"
            ))
            # Сносим старый уникальный индекс/constraint по одному key (любое из возможных имён)
            conn.execute(text("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'app_settings_key_key'
                          AND conrelid = 'app_settings'::regclass
                    ) THEN
                        ALTER TABLE app_settings DROP CONSTRAINT app_settings_key_key;
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'ix_app_settings_key'
                          AND conrelid = 'app_settings'::regclass
                    ) THEN
                        ALTER TABLE app_settings DROP CONSTRAINT ix_app_settings_key;
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM pg_indexes
                        WHERE indexname = 'ix_app_settings_key'
                          AND tablename = 'app_settings'
                    ) THEN
                        DROP INDEX ix_app_settings_key;
                    END IF;
                END$$;
            """))
            # Добавляем составной уникальный constraint
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'uq_app_settings_key_user'
                    ) THEN
                        ALTER TABLE app_settings
                        ADD CONSTRAINT uq_app_settings_key_user UNIQUE (key, user_id);
                    END IF;
                END$$;
            """))

            # 4. Legacy columns from v1
            conn.execute(text(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS cost_price NUMERIC(10, 2)"
            ))
            conn.execute(text(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)"
            ))
            conn.execute(text(
                "ALTER TABLE product_config ADD COLUMN IF NOT EXISTS "
                "competitor_select_rule VARCHAR(20) DEFAULT 'min_price'"
            ))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS competitor_entries (
                    id SERIAL PRIMARY KEY,
                    product_config_id INTEGER NOT NULL
                        REFERENCES product_config(id) ON DELETE CASCADE,
                    nm_id BIGINT NOT NULL,
                    alias VARCHAR(100)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_competitor_entries_product_config_id "
                "ON competitor_entries(product_config_id)"
            ))

            conn.commit()

            # 5. WB Catalog (кэш карточек для быстрого импорта)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS wb_catalog (
                    id SERIAL PRIMARY KEY,
                    account_id INTEGER NOT NULL
                        REFERENCES accounts(id) ON DELETE CASCADE,
                    nm_id BIGINT NOT NULL,
                    vendor_code VARCHAR(255) NOT NULL,
                    synced_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_wb_catalog_account_sku UNIQUE (account_id, vendor_code)
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_wb_catalog_account_id ON wb_catalog(account_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_wb_catalog_nm_id ON wb_catalog(account_id, nm_id)"
            ))
            conn.execute(text(
                "ALTER TABLE wb_catalog ADD COLUMN IF NOT EXISTS "
                "image_url VARCHAR(512)"
            ))
            # Backfill products.image_url from wb_catalog where available
            conn.execute(text("""
                UPDATE products p
                SET image_url = c.image_url
                FROM wb_catalog c
                WHERE p.account_id = c.account_id
                  AND p.nm_id = c.nm_id
                  AND p.image_url IS NULL
                  AND c.image_url IS NOT NULL
            """))
            conn.commit()

            # 6. Performance indexes
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_products_account_id ON products(account_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_price_snapshots_product_snapshot "
                "ON price_snapshots(product_id, snapshot_at DESC)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_repricer_runs_status_started "
                "ON repricer_runs(status, started_at DESC)"
            ))
            conn.commit()

            # 7. Seed admin user if no users exist
            count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            if count == 0:
                hashed = pwd.hash(settings.ADMIN_PASSWORD)
                conn.execute(text(
                    "INSERT INTO users (email, password_hash, is_active, is_admin) "
                    "VALUES (:email, :hash, TRUE, TRUE)"
                ), {"email": settings.ADMIN_EMAIL.lower(), "hash": hashed})
                conn.commit()
                log.info(f"[startup] Admin user created: {settings.ADMIN_EMAIL}")

            # 8. Encrypt existing plaintext WB API tokens (one-time migration)
            if settings.FERNET_KEY:
                try:
                    from app.utils.crypto import encrypt_field
                    rows = conn.execute(text(
                        "SELECT id, wb_api_token FROM accounts "
                        "WHERE wb_api_token IS NOT NULL AND wb_api_token NOT LIKE 'ENC:%'"
                    )).fetchall()
                    for row in rows:
                        encrypted = encrypt_field(row.wb_api_token)
                        conn.execute(text(
                            "UPDATE accounts SET wb_api_token = :val WHERE id = :id"
                        ), {"val": encrypted, "id": row.id})
                    if rows:
                        conn.commit()
                        log.info(f"[startup] Encrypted {len(rows)} WB API tokens")
                except Exception as e:
                    log.warning(f"[startup] Token encryption migration failed: {e}")
                    conn.rollback()

            # 9. Ставим в очередь синхронизацию каталога (фото) для аккаунтов,
            #    у которых есть товары без image_url.
            #    Задача запускается в фоне через Celery — не блокирует старт.
            try:
                missing_img_accounts = conn.execute(text("""
                    SELECT DISTINCT p.account_id
                    FROM products p
                    JOIN accounts a ON a.id = p.account_id
                    WHERE p.image_url IS NULL
                      AND a.is_active = TRUE
                """)).fetchall()
                if missing_img_accounts:
                    from app.tasks.catalog_sync import sync_wb_catalog_task
                    for row in missing_img_accounts:
                        sync_wb_catalog_task.delay(row.account_id)
                    log.info(
                        f"[startup] Queued catalog sync for "
                        f"{len(missing_img_accounts)} account(s) with missing product images"
                    )
            except Exception as e:
                log.warning(f"[startup] Failed to queue catalog sync for images: {e}")

        finally:
            # Снимаем advisory lock в любом случае (даже при ошибке)
            conn.execute(text("SELECT pg_advisory_unlock(42)"))
            conn.commit()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
