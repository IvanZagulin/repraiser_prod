from sqlalchemy import Column, Integer, Numeric, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class PriceChange(Base):
    """Лог изменения цены товара"""
    __tablename__ = "price_changes"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("repricer_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Старые значения
    old_seller_price = Column(Numeric(10, 2), nullable=False, comment="Старая цена продавца")
    old_final_price = Column(Numeric(10, 2), nullable=False, comment="Старая финальная цена")
    
    # Новые значения
    new_seller_price = Column(Numeric(10, 2), nullable=True, comment="Новая цена продавца")
    new_final_price = Column(Numeric(10, 2), nullable=True, comment="Новая финальная цена (расчётная)")
    
    # Причина изменения/пропуска
    reason_code = Column(String(50), nullable=False, comment="Код причины")
    reason_message = Column(Text, nullable=True, comment="Подробное описание")
    
    # Метаданные
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    run = relationship("RepricerRun", back_populates="price_changes")
    product = relationship("Product", back_populates="price_changes")

    def __repr__(self):
        return f"<PriceChange product_id={self.product_id} reason={self.reason_code}>"
