from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Numeric
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Product(Base):
    """Товар продавца"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    nm_id = Column(BigInteger, nullable=False, index=True, comment="Артикул WB (nmID)")
    seller_sku = Column(String(255), nullable=True, index=True, comment="Артикул продавца (может быть заполнен автоматически)")
    name = Column(String(500), nullable=True, comment="Название товара (может быть заполнено автоматически)")
    image_url = Column(String(500), nullable=True, comment="URL фото товара из WB Content API")
    cost_price = Column(Numeric(10, 2), nullable=True, comment="Себестоимость товара")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    account = relationship("Account", back_populates="products")
    config = relationship("ProductConfig", back_populates="product", uselist=False, cascade="all, delete-orphan")
    price_snapshots = relationship("PriceSnapshot", back_populates="product", cascade="all, delete-orphan")
    price_changes = relationship("PriceChange", back_populates="product", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Product {self.nm_id}: {self.seller_sku}>"
