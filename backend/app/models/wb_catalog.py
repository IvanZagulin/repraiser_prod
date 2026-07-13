from datetime import datetime, timezone
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, UniqueConstraint, Index
from app.database import Base

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WBCatalog(Base):
    """
    Кэш всех карточек WB для каждого аккаунта.
    Используется при импорте товаров — вместо отдельных запросов к WB API.
    Не отображается в списке товаров пользователя.
    """
    __tablename__ = "wb_catalog"

    id          = Column(Integer, primary_key=True, index=True)
    account_id  = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    nm_id       = Column(BigInteger, nullable=False)
    vendor_code = Column(String(255), nullable=False)
    image_url   = Column(String(512), nullable=True)   # первое фото из WB Content API
    name        = Column(String(512), nullable=True)   # название карточки из WB Content API
    subject_name = Column(String(255), nullable=True)  # предмет из WB Content API
    synced_at   = Column(DateTime, default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "vendor_code", name="uq_wb_catalog_account_sku"),
        Index("ix_wb_catalog_account_id", "account_id"),
        Index("ix_wb_catalog_nm_id", "account_id", "nm_id"),
    )
