from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.api import auth, accounts, products, repricer, export, settings as settings_api

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
        return response

app.add_middleware(SecurityHeadersMiddleware)


@app.get("/")
async def root():
    return {"status": "ok", "service": "WB Repricer API", "version": "2.0.0"}


@app.get("/api/health")
async def health_check():
    from app.services import proxy_pool
    proxy_stats = proxy_pool.get_stats() if proxy_pool._initialized else {"total": 0, "alive": 0, "dead": 0}
    return {
        "status": "healthy",
        "database": "connected",
        "services": {
            "wb_api": "operational",
            "catalog_parser": "operational",
            "proxy_pool": proxy_stats,
        },
    }


# Подключаем роутеры
app.include_router(auth.router,         prefix="/api/auth",     tags=["Authentication"])
app.include_router(accounts.router,     prefix="/api/accounts", tags=["Accounts"])
app.include_router(products.router,     prefix="/api/products", tags=["Products"])
app.include_router(repricer.router,     prefix="/api/repricer", tags=["Repricer"])
app.include_router(export.router,       prefix="/api/export",   tags=["Import/Export"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"])


@app.on_event("startup")
async def startup_db_migration():
    """Idempotent DB migrations + admin seed"""
    from app.database import engine
    from sqlalchemy import text
    from passlib.context import CryptContext
    import logging

    log = logging.getLogger(__name__)
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    with engine.connect() as conn:
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
        # Сносим старый уникальный индекс по одному key (если существует)
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

        # 5. Seed admin user if no users exist
        count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count == 0:
            hashed = pwd.hash(settings.ADMIN_PASSWORD)
            conn.execute(text(
                "INSERT INTO users (email, password_hash, is_active, is_admin) "
                "VALUES (:email, :hash, TRUE, TRUE)"
            ), {"email": settings.ADMIN_EMAIL.lower(), "hash": hashed})
            conn.commit()
            log.info(f"[startup] Admin user created: {settings.ADMIN_EMAIL}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
