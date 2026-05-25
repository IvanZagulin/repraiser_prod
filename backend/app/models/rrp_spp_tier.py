from sqlalchemy import Column, Integer, Numeric, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class RRPSPPTier(Base):
    """Ступень цены для конкретного диапазона СПП.

    Заполняется ОДНО из двух:
    - seller_price: прямая цена продавца (передаётся на WB как есть, до скидок и СПП)
    - rrp_price: целевая финальная цена покупателя (движок считает seller_price обратным расчётом)

    Диапазон: [spp_min, spp_max). spp_max=NULL — нет верхней границы.
    """
    __tablename__ = "rrp_spp_tiers"

    id                = Column(Integer, primary_key=True, index=True)
    product_config_id = Column(
        Integer,
        ForeignKey("product_config.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    spp_min      = Column(Numeric(5, 2), nullable=False,
                          comment="Нижняя граница СПП (включительно), %")
    spp_max      = Column(Numeric(5, 2), nullable=True,
                          comment="Верхняя граница СПП (не включая), NULL = без ограничения")
    # Ровно одно из двух должно быть заполнено:
    seller_price = Column(Numeric(10, 2), nullable=True,
                          comment="Цена продавца на WB (до скидок и СПП). Передаётся напрямую.")
    rrp_price    = Column(Numeric(10, 2), nullable=True,
                          comment="Целевая финальная цена покупателя. Движок считает seller_price обратно.")

    config = relationship("ProductConfig", back_populates="rrp_spp_tiers")

    __table_args__ = (
        CheckConstraint("spp_min >= 0",
                        name="check_rrp_tier_spp_min_non_negative"),
        CheckConstraint("spp_max IS NULL OR spp_max > spp_min",
                        name="check_rrp_tier_spp_max_gt_min"),
        CheckConstraint(
            "(rrp_price IS NOT NULL AND rrp_price > 0) OR "
            "(seller_price IS NOT NULL AND seller_price > 0)",
            name="check_rrp_tier_has_price",
        ),
    )

    def __repr__(self):
        price_info = (
            f"seller_price={self.seller_price}" if self.seller_price
            else f"rrp_price={self.rrp_price}"
        )
        return (
            f"<RRPSPPTier config_id={self.product_config_id} "
            f"spp=[{self.spp_min},{self.spp_max}) {price_info}>"
        )
