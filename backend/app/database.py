from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# SQLAlchemy engine — параметры пула зависят от диалекта
_db_url = settings.DATABASE_URL
_is_sqlite = _db_url.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        _db_url,
        connect_args={"check_same_thread": False},
    )
else:
    # Расчёт пула: pool_size=5 + max_overflow=10 = макс 15 соединений на процесс.
    # 4 gunicorn воркера × 15 = 60
    # 4 Celery воркера   × 15 = 60
    # scheduler + flower      ~ 15
    # Итого: ~135 < postgres max_connections=200 — запас есть.
    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,       # макс. ожидание свободного коннекта из пула (сек)
        pool_recycle=1800,     # пересоздавать коннекты каждые 30 мин (защита от stale)
        # 30s timeout на SQL-запросы — защита от зависших/долгих запросов
        connect_args={"options": "-c statement_timeout=30000"},
        # isolation_level="READ COMMITTED" — стандарт PostgreSQL, защита от dirty reads
        isolation_level="READ COMMITTED",
    )

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class для моделей
Base = declarative_base()


def get_db():
    """Dependency для получения DB session"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
