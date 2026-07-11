from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import utcnow
from .db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default="customer")  # customer | owner
    # 익명 풀에서 쓰는 비식별 키 — 원문 없는 anon_aspects/taste_profiles는 이 키로만 연결
    author_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50), default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    menus: Mapped[list["Menu"]] = relationship(back_populates="store")


class Menu(Base):
    __tablename__ = "menus"
    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    store: Mapped["Store"] = relationship(back_populates="menus")


class PersonalMemo(Base):
    """개인 저장소 — 원문 보존, 본인만 열람 (이중 저장의 개인 측)."""

    __tablename__ = "personal_memos"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    menu_ids: Mapped[list] = mapped_column(JSON, default=list)
    emotion: Mapped[str] = mapped_column(String(10))  # like | dislike
    text: Mapped[str] = mapped_column(Text, default="")
    chips: Mapped[list] = mapped_column(JSON, default=list)
    reorder_intent: Mapped[str] = mapped_column(String(15), default="none")  # pos|neg|conditional|none
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    aspects: Mapped[list["MemoAspect"]] = relationship(back_populates="memo", cascade="all, delete-orphan")


class MemoAspect(Base):
    """개인 메모의 태깅 레코드 (evidence는 원문 부분문자열)."""

    __tablename__ = "memo_aspects"
    id: Mapped[int] = mapped_column(primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("personal_memos.id"), index=True)
    scope: Mapped[str] = mapped_column(String(10))
    topic: Mapped[str] = mapped_column(String(20), index=True)
    value: Mapped[str | None] = mapped_column(String(20), nullable=True)
    polarity: Mapped[str] = mapped_column(String(10))
    intensity: Mapped[int] = mapped_column(Integer, default=1)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(10), default="chip")  # chip | text
    memo: Mapped["PersonalMemo"] = relationship(back_populates="aspects")


class AnonAspect(Base):
    """익명 공용 풀 — 원문 없음. author_hash + 태깅 레코드만 (이중 저장의 집단 측)."""

    __tablename__ = "anon_aspects"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_hash: Mapped[str] = mapped_column(String(64), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    menu_id: Mapped[int | None] = mapped_column(ForeignKey("menus.id"), nullable=True)
    memo_group: Mapped[str] = mapped_column(String(64), index=True)  # 같은 메모에서 나온 레코드 묶음
    scope: Mapped[str] = mapped_column(String(10))
    topic: Mapped[str] = mapped_column(String(20), index=True)
    value: Mapped[str | None] = mapped_column(String(20), nullable=True)
    polarity: Mapped[str] = mapped_column(String(10))
    intensity: Mapped[int] = mapped_column(Integer, default=1)
    normalized: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TasteProfile(Base):
    """입맛 프로필 — author_hash 단위 (로그인 유저와 익명 배경 풀이 같은 함수를 공유)."""

    __tablename__ = "taste_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    author_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
