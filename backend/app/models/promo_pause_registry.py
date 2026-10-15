from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, BigInteger, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PromoPauseRegistry(Base):
    """Automatic repricer pauses driven by the seller discount in WB Prices API."""

    __tablename__ = "promo_pause_registry"

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    product_config_id = Column(
        Integer,
        ForeignKey("product_config.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    product_id = Column(
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    account_id = Column(
        Integer,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    nm_id = Column(BigInteger, nullable=False, index=True)
    seller_sku = Column(String(255), nullable=True)
    discount_pct = Column(Numeric(5, 2), nullable=False)
    paused_at = Column(DateTime, default=_utcnow, nullable=False)
    last_checked_at = Column(DateTime, default=_utcnow, nullable=False)
    zero_streak = Column(Integer, default=0, nullable=False)
    is_paused = Column(Boolean, default=True, nullable=False, index=True)
    resumed_at = Column(DateTime, nullable=True)

    config = relationship("ProductConfig")
    product = relationship("Product")
    account = relationship("Account")

