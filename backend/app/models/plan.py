from sqlalchemy import Column, Integer, String, Numeric, Boolean, DateTime
from sqlalchemy.sql import func
from app.database import Base


class Plan(Base):
    """Тарифный план SaaS"""
    __tablename__ = "plans"

    id         = Column(Integer, primary_key=True, index=True)
    code       = Column(String(50), unique=True, nullable=False)   # "start", "business", "enterprise"
    name       = Column(String(100), nullable=False)               # "Старт", "Бизнес", "Энтерпрайз"
    max_sku    = Column(Integer, nullable=True)                    # NULL = безлимит
    max_accounts = Column(Integer, nullable=True)                  # NULL = безлимит
    price_rub  = Column(Numeric(10, 2), nullable=True)            # NULL = договорная
    is_active  = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<Plan {self.code}: {self.name}>"
