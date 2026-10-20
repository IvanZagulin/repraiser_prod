from sqlalchemy import Column, Integer, BigInteger, Numeric, DateTime, ForeignKey, String, Boolean, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PriceSnapshot(Base):
    """Снимок цены товара"""
    __tablename__ = "price_snapshots"
    __table_args__ = (
        # Ускоряет "get latest snapshot per product" запрос в list_products
        Index("ix_price_snapshots_product_snapshot", "product_id", "snapshot_at"),
    )

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

    # --- Ozon --------------------------------------------------------------
    # Заполняются задачей ozon.sync_prices и остаются пустыми для WB-товаров.
    old_price = Column(Numeric(10, 2), nullable=True, comment="Ozon: цена до скидки")
    min_price = Column(Numeric(10, 2), nullable=True, comment="Ozon: минимальная цена товара")

    ozon_competitor_min_price = Column(Numeric(10, 2), nullable=True,
                                       comment="Ozon: минимум конкурентов на самом Ozon")
    ozon_price_index = Column(Numeric(6, 3), nullable=True, comment="Ozon: индекс к конкурентам на Ozon")
    external_competitor_min_price = Column(Numeric(10, 2), nullable=True,
                                           comment="Ozon: минимум на других площадках")
    external_price_index = Column(Numeric(6, 3), nullable=True, comment="Ozon: индекс к другим площадкам")
    self_mp_min_price = Column(Numeric(10, 2), nullable=True,
                               comment="Ozon: наша же цена на других площадках")
    self_mp_price_index = Column(Numeric(6, 3), nullable=True, comment="Ozon: индекс к нашей цене на других МП")

    buyer_price_estimate = Column(Numeric(10, 2), nullable=True,
                                  comment="Ozon: расчётная цена покупателя (с Ozon Картой)")
    buyer_price_spread_pct = Column(Numeric(6, 2), nullable=True,
                                    comment="Ozon: расхождение оценок между индексами, % (мера доверия)")
    buyer_price_sources = Column(Integer, nullable=True,
                                 comment="Ozon: сколько индексов участвовало в оценке")
    coinvest_ratio = Column(Numeric(6, 4), nullable=True,
                            comment="Ozon: k = цена покупателя / цена продавца")
    ozon_site_price = Column(Numeric(10, 2), nullable=True,
                             comment="Ozon: цена на сайте из отчёта (прямой замер витрины)")
    commission_percent = Column(Numeric(5, 2), nullable=True, comment="Ozon: комиссия FBS, %")
    
    # Метаданные
    snapshot_at = Column(DateTime, default=_utcnow, nullable=False, index=True, comment="Время снимка")

    # Relationships
    product = relationship("Product", back_populates="price_snapshots")

    def __repr__(self):
        return f"<PriceSnapshot product_id={self.product_id} seller={self.seller_price} final={self.final_price}>"
