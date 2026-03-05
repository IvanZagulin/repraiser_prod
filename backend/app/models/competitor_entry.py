from sqlalchemy import Column, Integer, BigInteger, String, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class CompetitorEntry(Base):
    """Запись конкурента в списке для стратегии MultiCompetitor"""
    __tablename__ = "competitor_entries"

    id = Column(Integer, primary_key=True, index=True)
    product_config_id = Column(
        Integer,
        ForeignKey("product_config.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    nm_id = Column(BigInteger, nullable=False, comment="nmID конкурента на WB")
    alias = Column(String(100), nullable=True, comment="Произвольная метка")

    config = relationship("ProductConfig", back_populates="competitors")

    def __repr__(self):
        return f"<CompetitorEntry config_id={self.product_config_id} nm_id={self.nm_id}>"
