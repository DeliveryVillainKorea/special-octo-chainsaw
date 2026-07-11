from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..auth import get_current_user
from ..db import get_db
from ..models import PersonalMemo, Store, User

router = APIRouter(tags=["stores"])


@router.get("/stores")
def list_stores(ordered_only: bool = False, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """가게 리스트 (STEP 2) — 카드에 내 속마음 배지 소환. PoC에선 주문 이력 = 메모 이력."""
    stores = db.scalars(select(Store)).all()
    my_memos = db.scalars(select(PersonalMemo).where(PersonalMemo.user_id == user.id)
                          .order_by(PersonalMemo.created_at.desc())).all()
    latest_by_store: dict[int, PersonalMemo] = {}
    for m in my_memos:
        latest_by_store.setdefault(m.store_id, m)

    out = []
    for s in stores:
        memo = latest_by_store.get(s.id)
        if ordered_only and memo is None:
            continue
        out.append({
            "id": s.id, "name": s.name, "category": s.category,
            "menus": [{"id": m.id, "name": m.name} for m in s.menus],
            "ordered": memo is not None,
            "my_memo_badge": services._memo_badge(db, memo) if memo else None,
        })
    return out


@router.get("/stores/{store_id}")
def store_detail(store_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """가게 상세 (STEP 3-A/3-B) — 재주문이면 넛지, 첫 방문이면 집단×프로필 가이드."""
    store = db.get(Store, store_id)
    if store is None:
        raise HTTPException(404, "가게를 찾을 수 없어요")
    return {
        "id": store.id, "name": store.name, "category": store.category,
        "menus": [{"id": m.id, "name": m.name} for m in store.menus],
        "guide": services.build_guide(db, user, store),
        "chat_suggestions": services.chat_suggestions(db, store),
    }
