from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class ProductGroup(Base):
    __tablename__ = "product_groups"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    color = Column(String(7), nullable=False, default="#7c3aed")
    created_at = Column(DateTime, default=datetime.utcnow)

    memberships = relationship("ProductGroupMembership", back_populates="group", cascade="all, delete-orphan")


class ProductGroupMembership(Base):
    __tablename__ = "product_group_membership"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(Integer, ForeignKey("product_groups.id", ondelete="CASCADE"), nullable=False)

    group = relationship("ProductGroup", back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("product_id", "group_id", name="uq_product_group"),
    )
