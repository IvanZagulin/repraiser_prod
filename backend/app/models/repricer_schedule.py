from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class RepricerSchedule(Base):
    """Пользовательские настройки расписания репрайсера"""
    __tablename__ = "repricer_schedules"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    interval_minutes = Column(Integer, nullable=False, default=60)       # 15, 30, 60, 120
    work_start_hour  = Column(Integer, nullable=False, default=0)        # 0-23
    work_end_hour    = Column(Integer, nullable=False, default=24)       # 1-24
    timezone         = Column(String(50), nullable=False, default="Europe/Moscow")
    paused_days      = Column(String(50), nullable=True)                 # "0,6" = Вс, Сб
    is_active        = Column(Boolean, default=True, nullable=False)
    updated_at       = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", backref="repricer_schedule", uselist=False)

    def __repr__(self):
        return f"<RepricerSchedule user={self.user_id} every {self.interval_minutes}m>"
