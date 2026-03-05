from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Account(Base):
    """Аккаунт продавца WB"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, comment="Название аккаунта")
    wb_api_token = Column(Text, nullable=False, comment="JWT токен WB API")
    token_expires_at = Column(DateTime, nullable=True, comment="Срок действия токена")
    is_active = Column(Boolean, default=True, nullable=False, comment="Активен ли аккаунт")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    products = relationship("Product", back_populates="account", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Account {self.id}: {self.name}>"
