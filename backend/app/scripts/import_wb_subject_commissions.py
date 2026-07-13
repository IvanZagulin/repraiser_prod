import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.models.wb_subject_commission import WBSubjectCommission


DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "wb_subject_commissions.csv"


def _load_rows(path: Path):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            subject = " ".join((row.get("subject_name") or "").strip().split())
            commission = row.get("commission_pct")
            if not subject or not commission:
                continue
            yield {
                "subject_name": subject,
                "commission_pct": Decimal(str(commission).replace(",", ".")),
                "source": "google_sheet_2026_07_14",
                "updated_at": now,
            }


def main() -> int:
    rows = list(_load_rows(DATA_PATH))
    if not rows:
        print("No rows to import")
        return 0

    db = SessionLocal()
    try:
        stmt = insert(WBSubjectCommission).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[WBSubjectCommission.subject_name],
            set_={
                "commission_pct": stmt.excluded.commission_pct,
                "source": stmt.excluded.source,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        db.execute(stmt)
        db.commit()
        print(f"Imported WB subject commissions: {len(rows)}")
        return len(rows)
    finally:
        db.close()


if __name__ == "__main__":
    main()
