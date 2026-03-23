from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from app.database import Base

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PricingRule(Base):
    __tablename__ = "pricing_rules"

    id = Column(Integer, primary_key=True, index=True)
    product_config_id = Column(Integer, ForeignKey("product_config.id", ondelete="CASCADE"), nullable=False, index=True)
    priority = Column(Integer, nullable=False, default=0)
    condition_type = Column(String(30), nullable=False)
    condition_value = Column(String(50), nullable=True)
    action_type = Column(String(30), nullable=False)
    action_value = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=_utcnow)
