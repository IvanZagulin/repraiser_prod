from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, Numeric, Boolean, DateTime, ForeignKey
from app.database import Base


class CompetitorAlert(Base):
    __tablename__ = "competitor_alerts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    competitor_nm_id = Column(BigInteger, nullable=False)
    old_price = Column(Numeric(10, 2), nullable=False)
    new_price = Column(Numeric(10, 2), nullable=False)
    change_pct = Column(Numeric(5, 1), nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
