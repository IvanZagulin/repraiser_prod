"""Public endpoint for landing page lead submissions."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from slowapi import Limiter

from app.database import get_db
from app.models.lead import Lead
from app.utils import get_real_ip

router = APIRouter()
limiter = Limiter(key_func=get_real_ip)


class LeadCreate(BaseModel):
    name:  str
    email: EmailStr
    phone: str = ""


@router.post("")
@limiter.limit("5/minute")
async def submit_lead(request: Request, body: LeadCreate, db: Session = Depends(get_db)):
    lead = Lead(
        name=body.name.strip()[:255],
        email=body.email.lower().strip(),
        phone=body.phone.strip()[:50] if body.phone else None,
        source="landing",
        status="new",
    )
    db.add(lead)
    db.commit()
    return JSONResponse({"ok": True})
