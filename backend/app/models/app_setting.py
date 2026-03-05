from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class AppSetting(Base):
    """Настройки приложения (key-value, per-user)"""
    __tablename__ = "app_settings"
    __table_args__ = (
        UniqueConstraint("key", "user_id", name="uq_app_settings_key_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key = Column(String(100), nullable=False, index=True)
    value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    user = relationship("User", back_populates="settings")

    def __repr__(self):
        return f"<AppSetting user={self.user_id} {self.key}={self.value}>"
