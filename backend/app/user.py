from sqlalchemy import Column, Integer, String, Boolean, DateTime, SmallInteger
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    """Пользователь системы (SaaS-аккаунт)"""
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active     = Column(Boolean, default=True, nullable=False)
    is_admin      = Column(Boolean, default=False, nullable=False)
    is_verified   = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime, server_default=func.now(), nullable=False)

    # Email verification
    verification_code            = Column(String(6), nullable=True)
    verification_code_expires_at = Column(DateTime, nullable=True)
    verification_attempts        = Column(Integer, default=0, nullable=False)

    # Password reset
    reset_code            = Column(String(6), nullable=True)
    reset_code_expires_at = Column(DateTime, nullable=True)
    reset_attempts        = Column(Integer, default=0, nullable=False)

    # JWT invalidation: инкрементируется при смене пароля → старые токены перестают работать
    token_version = Column(SmallInteger, default=1, nullable=False)

    # Relationships
    accounts = relationship("Account", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("AppSetting", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.id}: {self.email}>"
