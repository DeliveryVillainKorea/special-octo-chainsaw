from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..auth import get_current_user
from ..db import get_db
from ..models import Menu, PersonalMemo, Store, User
from ..schemas import MemoIn

router = APIRouter(tags=["memos"])


@router.post("/memos")
def write_memo(body: MemoIn, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """속마음 리뷰 작성 (STEP 1) — 태깅 → 이중 저장 → 프로필 재계산까지 한 트랜잭션.

    응답의 profile_delta로 "매운맛 45→48점" 변화 연출 가능.
    """
    store = db.get(Store, body.store_id)
    if store is None:
        raise HTTPException(404, "가게를 찾을 수 없어요")
    if body.menu_ids:
        menus = db.scalars(select(Menu).where(Menu.id.in_(body.menu_ids))).all()
        if len(menus) != len(body.menu_ids) or any(m.store_id != store.id for m in menus):
            raise HTTPException(400, "이 가게의 메뉴가 아니에요")
    if not body.text.strip() and not body.chips:
        raise HTTPException(400, "텍스트나 태그 칩 중 하나는 있어야 해요")
    return services.create_memo(db, user, store, body.menu_ids, body.emotion,
                                body.text, body.chips)


@router.get("/memos")
def my_memos(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memos = db.scalars(select(PersonalMemo).where(PersonalMemo.user_id == user.id)
                       .order_by(PersonalMemo.created_at.desc())).all()
    return [services._memo_badge(db, m) | {"store_id": m.store_id} for m in memos]


@router.delete("/memos/{memo_id}")
def delete_memo(memo_id: int, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """삭제 전파 — 개인 메모 + 익명 풀 레코드 삭제 후 프로필 재계산."""
    memo = db.get(PersonalMemo, memo_id)
    if memo is None or memo.user_id != user.id:
        raise HTTPException(404, "메모를 찾을 수 없어요")
    from ..models import AnonAspect
    for row in db.scalars(select(AnonAspect).where(
            AnonAspect.author_hash == user.author_hash,
            AnonAspect.memo_group == f"m{memo.id}")).all():
        db.delete(row)
    db.delete(memo)
    db.flush()
    profile = services.recompute_user_profile(db, user)
    db.commit()
    return {"deleted": memo_id, "profile": profile}
