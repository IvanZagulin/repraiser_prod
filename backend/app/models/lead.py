from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base


class Lead(Base):
    """Заявка с лендинга quasar-x.ru"""
    __tablename__ = "leads"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(255), nullable=False)
    email      = Column(String(255), nullable=False, index=True)
    phone      = Column(String(50), nullable=True)
    source     = Column(String(100), nullable=True, default="landing")
    status     = Column(String(50), nullable=False, default="new")
    notes      = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
