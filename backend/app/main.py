from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.api import auth, accounts, products, repricer, export, settings as settings_api

app = FastAPI(
    title="WB Repricer API",
    description="SaaS система автоматического управления ценами на Wildberries",
    version="1.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# CORS middleware — ограничиваем origins
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# Security headers middleware
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
    """Health check endpoint"""
    return {
        "status": "ok",
        "service": "WB Repricer API",
        "version": "1.2.0"
    }


@app.get("/api/health")
async def health_check():
    """Detailed health check"""
    from app.services import proxy_pool
    
    proxy_stats = proxy_pool.get_stats() if proxy_pool._initialized else {"total": 0, "alive": 0, "dead": 0}
    
    return {
        "status": "healthy",
        "database": "connected",
        "services": {
            "wb_api": "operational",
            "catalog_parser": "operational",
            "proxy_pool": proxy_stats
        }
    }


# Подключаем роутеры
app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["Accounts"])
app.include_router(products.router, prefix="/api/products", tags=["Products"])
app.include_router(repricer.router, prefix="/api/repricer", tags=["Repricer"])
app.include_router(export.router, prefix="/api/export", tags=["Import/Export"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["Settings"])


@app.on_event("startup")
async def startup_db_migration():
    """Добавляем новые колонки и таблицы если их ещё нет (idempotent)"""
    from app.database import engine
    from sqlalchemy import text
    with engine.connect() as conn:
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
