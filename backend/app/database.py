from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

# SQLAlchemy engine
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,       # макс. ожидание свободного коннекта из пула (сек)
    pool_recycle=1800,     # пересоздавать коннекты каждые 30 мин (защита от stale)
    # 30s timeout на SQL-запросы — защита от зависших/долгих запросов
    connect_args={"options": "-c statement_timeout=30000"},
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
