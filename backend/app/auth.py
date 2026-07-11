"""PoC 더미 인증 — 시드 계정 + JWT. (실서비스 인증 아님: 해시는 sha256+salt)"""
import hashlib
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import JWT_ALGO, JWT_SECRET, TOKEN_TTL_HOURS, utcnow
from .db import get_db
from .models import User

SALT = "sokmaeum-poc"

# OpenAPI 보안 스킴 등록 → Swagger 우상단 Authorize 버튼에 토큰 입력 가능
bearer_scheme = HTTPBearer(auto_error=False, description="POST /auth/login 응답의 token")


def hash_password(raw: str) -> str:
    return hashlib.sha256((SALT + raw).encode()).hexdigest()


def create_token(user: User) -> str:
    payload = {"sub": str(user.id), "role": user.role, "nickname": user.nickname,
               "exp": utcnow() + timedelta(hours=TOKEN_TTL_HOURS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(401, "Authorization: Bearer <token> 헤더가 필요해요 "
                                 "(Swagger에서는 우상단 Authorize 버튼)")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "토큰이 유효하지 않아요")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(401, "존재하지 않는 사용자예요")
    return user


def require_owner(user: User = Depends(get_current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(403, "사장님 계정만 접근할 수 있어요")
    return user


def authenticate(db: Session, nickname: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.nickname == nickname))
    if user and user.password_hash == hash_password(password):
        return user
    return None
