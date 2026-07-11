from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import authenticate, create_token, get_current_user
from ..db import get_db
from ..models import User
from ..schemas import LoginIn

router = APIRouter(tags=["auth"])


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = authenticate(db, body.nickname, body.password)
    if user is None:
        raise HTTPException(401, "닉네임 또는 비밀번호가 맞지 않아요")
    return {"token": create_token(user),
            "user": {"id": user.id, "nickname": user.nickname, "role": user.role}}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "nickname": user.nickname, "role": user.role}
