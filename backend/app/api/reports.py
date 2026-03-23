"""Reports router — PDF report generation for clients."""

import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from app.database import get_db
from app.models import Account, Product, User
from app.models.price_change import PriceChange
from app.models.repricer_run import RepricerRun
from app.models.product_config import ProductConfig
from app.api.auth import get_current_user

router = APIRouter()


def _user_account_ids(db: Session, user: User) -> list:
    return [aid for (aid,) in db.query(Account.id).filter(Account.user_id == user.id).all()]


def _user_product_ids(db: Session, user: User) -> list:
    account_ids = _user_account_ids(db, user)
    if not account_ids:
        return []
    return [pid for (pid,) in db.query(Product.id).filter(Product.account_id.in_(account_ids)).all()]


@router.get("/generate")
def generate_report(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a branded PDF report for the given period."""
    from fpdf import FPDF

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=days)
    # Считаем account_ids один раз и переиспользуем — не вызываем _user_account_ids дважды
    account_ids = _user_account_ids(db, current_user)
    product_ids = (
        [pid for (pid,) in db.query(Product.id).filter(Product.account_id.in_(account_ids)).all()]
        if account_ids else []
    )

    # ── Gather data ──────────────────────────────────────────────────

    # Accounts
    accounts = db.query(Account).filter(Account.user_id == current_user.id).all()

    # Products with config
    products = []
    if product_ids:
        products = (
            db.query(Product)
            .filter(Product.id.in_(product_ids))
            .all()
        )

    # Runs in period
    runs = (
        db.query(RepricerRun)
        .filter(
            RepricerRun.user_id == current_user.id,
            RepricerRun.started_at >= since,
        )
        .order_by(RepricerRun.started_at.desc())
        .all()
    )
    total_runs = len(runs)
    completed_runs = sum(1 for r in runs if r.status == "completed")
    failed_runs = sum(1 for r in runs if r.status == "failed")
    total_updated = sum(r.updated_count or 0 for r in runs)

    # Price changes in period
    changes = []
    changes_count = 0
    top_changed = []
    if product_ids:
        changes_count = (
            db.query(func.count(PriceChange.id))
            .filter(
                PriceChange.product_id.in_(product_ids),
                PriceChange.changed_at >= since,
            )
            .scalar() or 0
        )

        # Top 10 most changed products
        rows = (
            db.query(
                PriceChange.product_id,
                func.count(PriceChange.id).label("cnt"),
            )
            .filter(
                PriceChange.product_id.in_(product_ids),
                PriceChange.changed_at >= since,
            )
            .group_by(PriceChange.product_id)
            .order_by(desc("cnt"))
            .limit(10)
            .all()
        )
        if rows:
            top_pids = [r.product_id for r in rows]
            prods = {
                p.id: p
                for p in db.query(Product).filter(Product.id.in_(top_pids)).all()
            }
            top_changed = [
                {"product": prods.get(r.product_id), "count": r.cnt}
                for r in rows
            ]

    # Strategy distribution
    strat_map = {"rrp": 0, "competitor": 0, "none": 0}
    if product_ids:
        configs = (
            db.query(ProductConfig.strategy, func.count(ProductConfig.id))
            .filter(ProductConfig.product_id.in_(product_ids))
            .group_by(ProductConfig.strategy)
            .all()
        )
        for strat, cnt in configs:
            if strat in strat_map:
                strat_map[strat] = cnt
            else:
                strat_map["none"] += cnt

    no_config = len(products) - sum(strat_map.values())
    if no_config > 0:
        strat_map["none"] += no_config

    # ── Build PDF ────────────────────────────────────────────────────

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Use built-in fonts for Cyrillic — we'll use cp1251 encoding trick
    # Actually, fpdf2 supports UTF-8 with built-in fonts via uni=True on add_font
    # For simplicity, let's add a Unicode font
    # fpdf2 has built-in Helvetica which doesn't support Cyrillic.
    # We need to use a Unicode font. Let's use the built-in DejaVu from fpdf2.
    try:
        pdf.add_font("DejaVu", "", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", uni=True)
        pdf.add_font("DejaVu", "B", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", uni=True)
        FONT = "DejaVu"
    except Exception:
        # Fallback: try to find any system font, or use Helvetica (no Cyrillic)
        FONT = "Helvetica"

    # Colors
    NEON = (0, 200, 255)
    DARK = (26, 26, 46)
    GRAY = (120, 130, 150)
    WHITE = (255, 255, 255)
    LIGHT_BG = (245, 247, 250)

    page_w = pdf.w - 20  # usable width (margins 10 each side)

    # ── Header ──
    pdf.set_fill_color(*DARK)
    pdf.rect(0, 0, pdf.w, 40, "F")

    pdf.set_xy(10, 8)
    pdf.set_font(FONT, "B", 22)
    pdf.set_text_color(*NEON)
    pdf.cell(0, 10, "QUASAR-X", ln=True)

    pdf.set_xy(10, 20)
    pdf.set_font(FONT, "", 10)
    pdf.set_text_color(*WHITE)
    period_from = since.strftime("%d.%m.%Y")
    period_to = now.strftime("%d.%m.%Y")
    pdf.cell(0, 8, f"Отчёт за период: {period_from} — {period_to}", ln=True)

    pdf.set_xy(pdf.w - 80, 20)
    pdf.set_font(FONT, "", 9)
    pdf.set_text_color(150, 160, 180)
    pdf.cell(70, 8, f"Пользователь: {current_user.email}", align="R")

    pdf.set_y(48)

    # ── Summary cards ──
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 10, "Сводка", ln=True)
    pdf.ln(2)

    card_w = page_w / 4
    card_h = 28
    cards = [
        ("Запусков", str(total_runs), NEON),
        ("Успешных", f"{completed_runs} ({round(completed_runs / total_runs * 100) if total_runs else 0}%)", (34, 197, 94)),
        ("Обновлений", f"{total_updated:,}".replace(",", " "), (0, 102, 255)),
        ("Изменений цен", str(changes_count), (139, 92, 246)),
    ]

    x_start = 10
    y_start = pdf.get_y()
    for i, (label, value, color) in enumerate(cards):
        x = x_start + i * card_w
        # Card background
        pdf.set_fill_color(*LIGHT_BG)
        pdf.rect(x, y_start, card_w - 3, card_h, "F")
        # Label
        pdf.set_xy(x + 4, y_start + 3)
        pdf.set_font(FONT, "", 8)
        pdf.set_text_color(*GRAY)
        pdf.cell(card_w - 8, 5, label)
        # Value
        pdf.set_xy(x + 4, y_start + 12)
        pdf.set_font(FONT, "B", 16)
        pdf.set_text_color(*color)
        pdf.cell(card_w - 8, 10, value)

    pdf.set_y(y_start + card_h + 8)

    # ── Products & Strategy ──
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 10, "Товары и стратегии", ln=True)
    pdf.ln(2)

    strat_labels = {"rrp": "РРЦ", "competitor": "Конкурент", "none": "Без стратегии"}
    strat_colors = {"rrp": NEON, "competitor": (0, 102, 255), "none": (200, 200, 210)}

    y_strat = pdf.get_y()
    # Total products card
    pdf.set_fill_color(*LIGHT_BG)
    pdf.rect(10, y_strat, page_w / 3 - 2, 20, "F")
    pdf.set_xy(14, y_strat + 3)
    pdf.set_font(FONT, "", 8)
    pdf.set_text_color(*GRAY)
    pdf.cell(50, 5, "Всего товаров")
    pdf.set_xy(14, y_strat + 10)
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*DARK)
    pdf.cell(50, 7, str(len(products)))

    # Strategy breakdown
    sx = 10 + page_w / 3 + 4
    for strat, label in strat_labels.items():
        cnt = strat_map.get(strat, 0)
        color = strat_colors[strat]
        pdf.set_fill_color(*LIGHT_BG)
        pdf.rect(sx, y_strat, page_w / 3 - 2, 20, "F")
        pdf.set_xy(sx + 4, y_strat + 3)
        pdf.set_font(FONT, "", 8)
        pdf.set_text_color(*GRAY)
        pdf.cell(50, 5, label)
        pdf.set_xy(sx + 4, y_strat + 10)
        pdf.set_font(FONT, "B", 14)
        pdf.set_text_color(*color)
        pdf.cell(50, 7, str(cnt))
        sx += page_w / 3 + 2

    pdf.set_y(y_strat + 28)

    # ── Accounts ──
    pdf.set_font(FONT, "B", 14)
    pdf.set_text_color(*DARK)
    pdf.cell(0, 10, "Аккаунты WB", ln=True)
    pdf.ln(2)

    if accounts:
        # Table header
        col_w = [page_w * 0.05, page_w * 0.35, page_w * 0.3, page_w * 0.3]
        pdf.set_fill_color(*DARK)
        pdf.set_text_color(*WHITE)
        pdf.set_font(FONT, "B", 9)
        headers = ["#", "Название", "Статус", "Добавлен"]
        for j, h in enumerate(headers):
            pdf.cell(col_w[j], 8, h, border=0, fill=True)
        pdf.ln()

        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9)
        for idx, acc in enumerate(accounts, 1):
            if pdf.get_y() > 260:
                pdf.add_page()
            bg = LIGHT_BG if idx % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            pdf.cell(col_w[0], 7, str(idx), fill=True)
            pdf.cell(col_w[1], 7, str(acc.name or "—")[:30], fill=True)
            status = "Активен" if acc.is_active else "Неактивен"
            pdf.cell(col_w[2], 7, status, fill=True)
            created = acc.created_at.strftime("%d.%m.%Y") if acc.created_at else "—"
            pdf.cell(col_w[3], 7, created, fill=True)
            pdf.ln()
    else:
        pdf.set_font(FONT, "", 10)
        pdf.set_text_color(*GRAY)
        pdf.cell(0, 8, "Нет подключённых аккаунтов", ln=True)

    pdf.ln(6)

    # ── Top Changed Products ──
    if top_changed:
        pdf.set_font(FONT, "B", 14)
        pdf.set_text_color(*DARK)
        pdf.cell(0, 10, "Топ-10 товаров по изменениям цен", ln=True)
        pdf.ln(2)

        col_w2 = [page_w * 0.05, page_w * 0.15, page_w * 0.5, page_w * 0.15, page_w * 0.15]
        pdf.set_fill_color(*DARK)
        pdf.set_text_color(*WHITE)
        pdf.set_font(FONT, "B", 9)
        for j, h in enumerate(["#", "NM ID", "Название", "Изменений", "Доля"]):
            pdf.cell(col_w2[j], 8, h, fill=True)
        pdf.ln()

        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9)
        for idx, item in enumerate(top_changed, 1):
            if pdf.get_y() > 260:
                pdf.add_page()
            bg = LIGHT_BG if idx % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            p = item["product"]
            cnt = item["count"]
            share = f"{round(cnt / changes_count * 100, 1)}%" if changes_count else "—"
            pdf.cell(col_w2[0], 7, str(idx), fill=True)
            pdf.cell(col_w2[1], 7, str(p.nm_id if p else "—"), fill=True)
            name = str(p.name or p.seller_sku or "—")[:40] if p else "—"
            pdf.cell(col_w2[2], 7, name, fill=True)
            pdf.cell(col_w2[3], 7, str(cnt), fill=True)
            pdf.cell(col_w2[4], 7, share, fill=True)
            pdf.ln()

        pdf.ln(6)

    # ── Runs History (last 20) ──
    if runs:
        pdf.set_font(FONT, "B", 14)
        pdf.set_text_color(*DARK)
        pdf.cell(0, 10, "История запусков (последние 20)", ln=True)
        pdf.ln(2)

        col_w3 = [page_w * 0.08, page_w * 0.25, page_w * 0.15, page_w * 0.17, page_w * 0.17, page_w * 0.18]
        pdf.set_fill_color(*DARK)
        pdf.set_text_color(*WHITE)
        pdf.set_font(FONT, "B", 9)
        for j, h in enumerate(["#", "Дата", "Статус", "Обновлено", "Всего", "Тип"]):
            pdf.cell(col_w3[j], 8, h, fill=True)
        pdf.ln()

        pdf.set_text_color(*DARK)
        pdf.set_font(FONT, "", 9)
        for idx, run in enumerate(runs[:20], 1):
            if pdf.get_y() > 260:
                pdf.add_page()
            bg = LIGHT_BG if idx % 2 == 0 else WHITE
            pdf.set_fill_color(*bg)
            pdf.cell(col_w3[0], 7, str(run.id), fill=True)
            started = run.started_at.strftime("%d.%m %H:%M") if run.started_at else "—"
            pdf.cell(col_w3[1], 7, started, fill=True)
            status_map = {"completed": "Успех", "failed": "Ошибка", "running": "В процессе"}
            pdf.cell(col_w3[2], 7, status_map.get(run.status, run.status or "—"), fill=True)
            pdf.cell(col_w3[3], 7, str(run.updated_count or 0), fill=True)
            pdf.cell(col_w3[4], 7, str(run.total_products or 0), fill=True)
            rtype = "Авто" if run.run_type == "scheduled" else "Ручной"
            pdf.cell(col_w3[5], 7, rtype, fill=True)
            pdf.ln()

    # ── Footer ──
    pdf.set_y(-25)
    pdf.set_font(FONT, "", 8)
    pdf.set_text_color(*GRAY)
    pdf.cell(0, 5, f"QUASAR-X Repricer — Автоматическое управление ценами на Wildberries", align="C", ln=True)
    generated = now.strftime("%d.%m.%Y %H:%M")
    pdf.cell(0, 5, f"Отчёт сгенерирован: {generated} UTC", align="C")

    # ── Output ──
    buf = io.BytesIO()
    pdf.output(buf)
    buf.seek(0)

    filename = f"QUASAR-X_report_{period_from}_{period_to}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )
