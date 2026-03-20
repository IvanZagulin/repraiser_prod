from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field
from datetime import datetime

from app.database import get_db
from app.models import Product, Account
from app.models.product_group import ProductGroup, ProductGroupMembership
from app.models.user import User
from app.api.auth import get_current_user

router = APIRouter()


# --- Schemas ---

class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    color: str = Field(default="#7c3aed", pattern=r"^#[0-9a-fA-F]{6}$")


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class GroupResponse(BaseModel):
    id: int
    name: str
    color: str
    product_count: int = 0
    created_at: datetime


class ProductIdsRequest(BaseModel):
    product_ids: List[int] = Field(..., min_length=1)


# --- Helpers ---

def _user_group(db: Session, group_id: int, user: User) -> ProductGroup:
    group = db.query(ProductGroup).filter(
        ProductGroup.id == group_id, ProductGroup.user_id == user.id
    ).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


# --- Endpoints ---

@router.get("", response_model=List[GroupResponse])
async def list_groups(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    rows = (
        db.query(
            ProductGroup,
            func.count(ProductGroupMembership.id).label("cnt"),
        )
        .outerjoin(ProductGroupMembership, ProductGroup.id == ProductGroupMembership.group_id)
        .filter(ProductGroup.user_id == current_user.id)
        .group_by(ProductGroup.id)
        .all()
    )
    return [
        GroupResponse(
            id=g.id, name=g.name, color=g.color,
            product_count=cnt, created_at=g.created_at,
        )
        for g, cnt in rows
    ]


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
async def create_group(
    data: GroupCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    group = ProductGroup(user_id=current_user.id, name=data.name, color=data.color)
    db.add(group)
    db.commit()
    db.refresh(group)
    return GroupResponse(
        id=group.id, name=group.name, color=group.color,
        product_count=0, created_at=group.created_at,
    )


@router.put("/{group_id}", response_model=GroupResponse)
async def update_group(
    group_id: int,
    data: GroupUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    group = _user_group(db, group_id, current_user)
    if data.name is not None:
        group.name = data.name
    if data.color is not None:
        group.color = data.color
    db.commit()
    db.refresh(group)
    cnt = db.query(func.count(ProductGroupMembership.id)).filter(
        ProductGroupMembership.group_id == group.id
    ).scalar()
    return GroupResponse(
        id=group.id, name=group.name, color=group.color,
        product_count=cnt, created_at=group.created_at,
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    group = _user_group(db, group_id, current_user)
    db.delete(group)
    db.commit()
    return None


@router.post("/{group_id}/products", status_code=status.HTTP_200_OK)
async def add_products_to_group(
    group_id: int,
    data: ProductIdsRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    group = _user_group(db, group_id, current_user)

    # Проверяем что все товары принадлежат пользователю
    valid_ids = [
        pid for (pid,) in
        db.query(Product.id)
        .join(Account, Product.account_id == Account.id)
        .filter(Product.id.in_(data.product_ids), Account.user_id == current_user.id)
        .all()
    ]

    existing = {
        m.product_id for m in
        db.query(ProductGroupMembership)
        .filter(
            ProductGroupMembership.group_id == group.id,
            ProductGroupMembership.product_id.in_(valid_ids),
        )
        .all()
    }

    added = 0
    for pid in valid_ids:
        if pid not in existing:
            db.add(ProductGroupMembership(product_id=pid, group_id=group.id))
            added += 1
    db.commit()
    return {"added": added}


@router.delete("/{group_id}/products", status_code=status.HTTP_200_OK)
async def remove_products_from_group(
    group_id: int,
    data: ProductIdsRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    group = _user_group(db, group_id, current_user)
    deleted = (
        db.query(ProductGroupMembership)
        .filter(
            ProductGroupMembership.group_id == group.id,
            ProductGroupMembership.product_id.in_(data.product_ids),
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"removed": deleted}
