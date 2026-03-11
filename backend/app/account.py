from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base
from app.utils.crypto import EncryptedText


class Account(Base):
    """Аккаунт продавца WB"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False, comment="Название аккаунта")
    wb_api_token = Column(EncryptedText, nullable=False, comment="JWT токен WB API (зашифрован Fernet)")
    token_expires_at = Column(DateTime, nullable=True, comment="Срок действия токена")
    is_active = Column(Boolean, default=True, nullable=False, comment="Активен ли аккаунт")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="accounts")
    products = relationship("Product", back_populates="account", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Account {self.id}: {self.name}>"
