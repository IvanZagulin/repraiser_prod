import re
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    force=True,
)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.config import settings
from app.utils import get_real_ip
from app.api import (
    auth, accounts, products, repricer, export,
    settings as settings_api, admin, schedule, analytics, bulk, activity,
    reports, alerts, groups, audit,
)


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
    openapi_url="/api/openapi.json",
    redirect_slashes=False,
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
app.include_router(auth.router,         prefix="/api/auth",      tags=["Authentication"])
app.include_router(accounts.router,     prefix="/api/accounts",  tags=["Accounts"])
app.include_router(products.router,     prefix="/api/products",  tags=["Products"])
app.include_router(repricer.router,     prefix="/api/repricer",  tags=["Repricer"])
app.include_router(export.router,       prefix="/api/export",    tags=["Import/Export"])
app.include_router(settings_api.router, prefix="/api/settings",  tags=["Settings"])
app.include_router(admin.router,        prefix="/api/admin",     tags=["Admin"])
app.include_router(schedule.router,     prefix="/api/schedule",  tags=["Schedule"])
app.include_router(analytics.router,    prefix="/api/analytics", tags=["Analytics"])
app.include_router(bulk.router,         prefix="/api/products/bulk", tags=["Bulk Operations"])
app.include_router(activity.router,     prefix="/api/activity",  tags=["Activity Log"])
app.include_router(reports.router,      prefix="/api/reports",   tags=["Reports"])
app.include_router(alerts.router,       prefix="/api/alerts",    tags=["Competitor Alerts"])
app.include_router(groups.router,       prefix="/api/groups",    tags=["Product Groups"])
app.include_router(audit.router,        prefix="/api/audit",     tags=["Audit Log"])


@app.on_event("startup")
async def startup_db_migration():
    """DB migrations via Alembic + non-DDL seed/ops.

    DDL теперь живёт в alembic/versions/. Здесь только:
      1. Bootstrap минимальной таблицы users (чтобы миграция 006 не упала
         из-за отсутствия таблицы при fresh install)
      2. alembic upgrade head — применяет все новые миграции
      3. Seed admin-юзера (если БД пустая)
      4. One-time шифрование токенов WB
      5. Celery-задача синхронизации фото для аккаунтов без image_url
    """
    import os
    from app.database import engine
    from sqlalchemy import text
    from passlib.context import CryptContext
    import logging

    log = logging.getLogger(__name__)
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    with engine.connect() as conn:
        # pg_advisory_lock защищает от гонки при --workers > 1
        conn.execute(text("SELECT pg_advisory_lock(42)"))
        try:
            # 1. Bootstrap users (минимально, только чтоб FK в миграции 006 не упал)
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    email         VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """))
            conn.commit()

            # 2. Alembic upgrade head — весь DDL теперь в миграциях
            try:
                from alembic.config import Config
                from alembic import command as alembic_command
                backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                alembic_cfg = Config(os.path.join(backend_dir, "alembic.ini"))
                alembic_command.upgrade(alembic_cfg, "head")
                log.info("[startup] alembic upgrade head — OK")
            except Exception as e:
                log.error(f"[startup] alembic upgrade head failed: {e}", exc_info=True)
                raise

            # 3. Seed admin user if no users exist
            count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            if count == 0:
                hashed = pwd.hash(settings.ADMIN_PASSWORD)
                conn.execute(text(
                    "INSERT INTO users (email, password_hash, is_active, is_admin) "
                    "VALUES (:email, :hash, TRUE, TRUE)"
                ), {"email": settings.ADMIN_EMAIL.lower(), "hash": hashed})
                conn.commit()
                log.info(f"[startup] Admin user created: {settings.ADMIN_EMAIL}")

            # 4. One-time: шифруем plaintext WB API токены
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

            # 5. Ставим в очередь синхронизацию фото для аккаунтов без image_url
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
            conn.execute(text("SELECT pg_advisory_unlock(42)"))
            conn.commit()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
