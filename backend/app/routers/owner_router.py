from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..auth import require_owner
from ..db import get_db
from ..models import Store, User

router = APIRouter(tags=["owner"])


@router.get("/owner/stores")
def my_stores(user: User = Depends(require_owner), db: Session = Depends(get_db)):
    stores = db.scalars(select(Store).where(Store.owner_id == user.id)).all()
    return [{"id": s.id, "name": s.name} for s in stores]


@router.get("/owner/stores/{store_id}/dashboard")
def dashboard(store_id: int, user: User = Depends(require_owner),
              db: Session = Depends(get_db)):
    """사장님 대시보드 — 개별 원문 없이 '짜다 15건' 식 익명 집계만."""
    store = db.get(Store, store_id)
    if store is None or store.owner_id != user.id:
        raise HTTPException(404, "내 가게가 아니거나 없는 가게예요")
    return services.owner_dashboard(db, store)
