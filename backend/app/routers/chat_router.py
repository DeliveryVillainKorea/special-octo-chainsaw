from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import services
from ..auth import get_current_user
from ..db import get_db
from ..models import Store, User
from ..schemas import ChatIn

router = APIRouter(tags=["chat"])


@router.post("/chat")
def chat(body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI 챗봇 (STEP 3-C) — 질문분류 → 이웃 우선 집계(부족 시 전체 폴백) → 답변 합성."""
    store = db.get(Store, body.store_id)
    if store is None:
        raise HTTPException(404, "가게를 찾을 수 없어요")
    return services.build_chat_answer(db, user, store, body.question)
