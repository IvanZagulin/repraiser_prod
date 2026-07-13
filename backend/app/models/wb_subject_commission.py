from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, Numeric, String

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WBSubjectCommission(Base):
    """Commission percent by WB subject."""

    __tablename__ = "wb_subject_commissions"

    id = Column(Integer, primary_key=True, index=True)
    subject_name = Column(String(255), nullable=False, unique=True, index=True)
    commission_pct = Column(Numeric(6, 2), nullable=False)
    source = Column(String(100), nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    def __repr__(self):
        return f"<WBSubjectCommission {self.subject_name}: {self.commission_pct}>"
