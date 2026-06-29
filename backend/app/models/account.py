from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.crypto import EncryptedText


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(Base):
    """Seller account for a supported marketplace."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False, comment="Account name")
    marketplace = Column(String(20), nullable=False, default="wb", server_default="wb", index=True)

    wb_api_token = Column(EncryptedText, nullable=True, comment="WB API JWT token")
    ozon_client_id = Column(EncryptedText, nullable=True, comment="Ozon Seller API Client-Id")
    ozon_api_key = Column(EncryptedText, nullable=True, comment="Ozon Seller API Api-Key")

    token_expires_at = Column(DateTime, nullable=True, comment="WB token expiration")
    is_active = Column(Boolean, default=True, nullable=False, comment="Whether account is active")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    user = relationship("User", back_populates="accounts")
    products = relationship("Product", back_populates="account", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Account {self.id}: {self.name} ({self.marketplace})>"
