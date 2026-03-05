from sqlalchemy import Column, Integer, BigInteger, Numeric, DateTime, ForeignKey, String, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class PriceSnapshot(Base):
    """Снимок цены товара"""
    __tablename__ = "price_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Цены (в рублях)
    seller_price = Column(Numeric(10, 2), nullable=False, comment="Цена продавца (basic)")
    final_price = Column(Numeric(10, 2), nullable=False, comment="Финальная цена покупателя (product)")
    discount = Column(Numeric(5, 2), nullable=True, comment="Скидка продавца (%)")
    spp = Column(Numeric(5, 2), nullable=True, comment="СПП (%)")
    
    # Данные конкурента (если стратегия competitor)
    competitor_nm_id = Column(BigInteger, nullable=True, comment="nmID конкурента")
    competitor_price = Column(Numeric(10, 2), nullable=True, comment="Цена конкурента")
    competitor_in_stock = Column(Boolean, nullable=True, comment="Наличие у конкурента")
    
    # Статус товара
    is_in_promo = Column(Boolean, default=False, nullable=False, comment="Участвует в акции WB")
    total_quantity = Column(Integer, nullable=True, comment="Остаток на складах WB")
    
    # Метаданные
    snapshot_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True, comment="Время снимка")

    # Relationships
    product = relationship("Product", back_populates="price_snapshots")

    def __repr__(self):
        return f"<PriceSnapshot product_id={self.product_id} seller={self.seller_price} final={self.final_price}>"
