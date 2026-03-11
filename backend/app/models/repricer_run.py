from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class RepricerRun(Base):
    """Прогон репрайсера"""
    __tablename__ = "repricer_runs"

    id = Column(Integer, primary_key=True, index=True)

    # Владелец прогона (для изоляции данных между пользователями)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Тип запуска
    run_type = Column(String(20), nullable=False, comment="Тип: scheduled или manual")

    # Статистика
    total_products = Column(Integer, default=0, comment="Всего товаров обработано")
    updated_count = Column(Integer, default=0, comment="Цены изменены")
    skipped_count = Column(Integer, default=0, comment="Пропущено")
    error_count = Column(Integer, default=0, comment="Ошибок")

    # Статус
    status = Column(String(20), default="running", nullable=False, comment="Статус: running, completed, failed")
    error_message = Column(Text, nullable=True, comment="Сообщение об ошибке")

    # Метаданные
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)

    # Relationships
    price_changes = relationship("PriceChange", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<RepricerRun {self.id} user={self.user_id} {self.run_type} {self.status}>"
