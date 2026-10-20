from sqlalchemy import Column, Integer, String, Boolean, Numeric, BigInteger, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from app.database import Base

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProductConfig(Base):
    """Конфигурация репрайсера для товара"""
    __tablename__ = "product_config"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Основные настройки
    strategy = Column(String(20), nullable=False, comment="Стратегия: rrp или competitor")
    is_active = Column(Boolean, default=True, nullable=False, comment="Включен ли репрайсер")

    # Защитные лимиты
    min_price = Column(Numeric(10, 2), nullable=False, comment="Минимальная цена (0 для отключения)")
    max_price = Column(Numeric(10, 2), nullable=True, comment="Максимальная цена")

    # Настройки стратегии RRP
    rrp_price = Column(Numeric(10, 2), nullable=True,
                       comment="Рекомендованная розничная цена (базовая, если нет ступеней по СПП)")
    rrp_tolerance = Column(Numeric(10, 2), default=5, nullable=True, comment="Допуск для РРЦ (в рублях)")
    rrp_consider_wallet = Column(Boolean, default=False, nullable=False,
                                  comment="Учитывать скидку кошелька WB при расчёте РРЦ")

    # Настройки стратегии Competitor
    competitor_nm_id = Column(BigInteger, nullable=True, comment="nmID конкурента (legacy, одиночный)")
    competitor_offset_type = Column(String(5), nullable=True, comment="Тип смещения: rub или pct")
    competitor_offset_direction = Column(String(6), nullable=True, comment="Направление: above или below")
    competitor_offset_value = Column(Numeric(10, 2), nullable=True, comment="Величина смещения")
    competitor_select_rule = Column(String(20), default="min_price", nullable=True,
                                    comment="Правило выбора цены: min_price, avg_price, cheapest_instock")

    # Ночное повышение цены (для поднятия средней цены перед акциями WB)
    night_price_enabled = Column(Boolean, default=False, nullable=False,
                                  comment="Включить ночное повышение цены")
    night_start_msk = Column(String(5), nullable=True,
                              comment="Начало ночного окна МСК, формат HH:MM")
    night_end_msk = Column(String(5), nullable=True,
                            comment="Конец ночного окна МСК, формат HH:MM")
    night_seller_price = Column(Numeric(10, 2), nullable=True,
                                 comment="Цена продавца в ночное окно (завышенная для поднятия средней)")

    # Защита маржи
    skip_margin_check = Column(Boolean, default=False, nullable=False,
                                comment='Отключить защиту маржи для этого товара')

    # Ozon: замкнутый цикл. Доля доинвестирования (k = цена_покупателя /
    # цена_продавца) не задаётся заранее и слегка плавает, поэтому храним
    # реально сложившееся значение и доводим цену по нему, обновляя его
    # каждым прогоном (EMA). См. RepricerEngine._ozon_rrp_closed_loop.
    ozon_realized_k = Column(Numeric(6, 4), nullable=True,
                             comment='Ozon: сглаженный k = цена покупателя / цена продавца')
    ozon_k_updated_at = Column(DateTime, nullable=True,
                               comment='Ozon: когда обновлён realized_k')
    # Поправка индекс->реальная витрина, если её замерили парсингом
    # (по умолчанию NULL = 1.0, т.е. верим индексу как есть).
    # Применяется только когда прямого замера витрины нет: при живом
    # ozon_site_price поправлять нечего, там уже настоящая цена.
    # Замерять только в том регионе, цену которого отдаёт отчёт Ozon:
    # карточка в другом регионе стоит иначе, и такая поправка ломает расчёт
    # (см. app/scripts/ozon_calibrate_storefront.py).
    ozon_storefront_factor = Column(Numeric(6, 4), nullable=True,
                                    comment='Ozon: реальная витрина / витрина по индексу')

    # Прямой замер витрины: колонка «Цена на сайте, ₽» из отчёта Ozon
    # (см. app/services/ozon_site_price.py). Цену продавца на момент замера
    # держим рядом — по ней видно, успел ли Ozon пересчитать витрину после
    # того, как мы поменяли цену, или замер ещё относится к старой.
    ozon_site_price = Column(Numeric(10, 2), nullable=True,
                             comment='Ozon: цена на сайте из отчёта (цена покупателя)')
    ozon_site_price_at = Column(DateTime, nullable=True,
                                comment='Ozon: когда снят ozon_site_price')
    ozon_site_seller_price = Column(Numeric(10, 2), nullable=True,
                                    comment='Ozon: цена продавца в момент замера витрины')

    # Имитация зачёркнутой цены конкурента: priceU = competitor.basic,
    # скидка продавца подбирается под target_final с учётом нашего фактического СПП.
    match_competitor_basic = Column(Boolean, default=False, nullable=False,
                                     comment='Имитировать зачёркнутую цену конкурента (priceU+discount)')

    # WB Price Quarantine: WB временно отвергает изменения цены этого товара
    # (после серии резких снижений). Выставляется автоматически при отказе WB,
    # репрайсер пропускает товар до истечения времени.
    wb_blocked_until = Column(DateTime, nullable=True,
                              comment='WB Price Quarantine — не трогать цену до этого времени')

    # Метаданные
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    # Relationships
    product = relationship("Product", back_populates="config")
    competitors = relationship("CompetitorEntry", back_populates="config",
                               cascade="all, delete-orphan", lazy="select")
    rrp_spp_tiers = relationship("RRPSPPTier", back_populates="config",
                                  cascade="all, delete-orphan", lazy="select",
                                  order_by="RRPSPPTier.spp_min")

    # Constraints
    __table_args__ = (
        CheckConstraint("strategy IN ('rrp', 'competitor')", name="check_strategy"),
        CheckConstraint("min_price >= 0", name="check_min_price_non_negative"),
        CheckConstraint("max_price IS NULL OR max_price >= min_price", name="check_max_price"),
        CheckConstraint("competitor_offset_type IS NULL OR competitor_offset_type IN ('rub', 'pct')",
                        name="check_offset_type"),
        CheckConstraint("competitor_offset_direction IS NULL OR competitor_offset_direction IN ('above', 'below')",
                        name="check_offset_direction"),
    )

    def __repr__(self):
        return f"<ProductConfig product_id={self.product_id} strategy={self.strategy}>"
