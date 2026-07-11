from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .taxonomy import CHIP_MAP


class LoginIn(BaseModel):
    nickname: str
    password: str


class MemoIn(BaseModel):
    store_id: int
    menu_ids: list[int] = []
    emotion: Literal["like", "dislike"]
    text: str = Field("", max_length=1000)  # 작성 화면 스펙: 최대 1,000자
    chips: list[str] = []

    @field_validator("chips")
    @classmethod
    def chips_must_be_known(cls, v: list[str]) -> list[str]:
        unknown = [c for c in v if c not in CHIP_MAP]
        if unknown:
            raise ValueError(f"알 수 없는 태그 칩: {unknown} (허용: {list(CHIP_MAP)})")
        return v


class ChatIn(BaseModel):
    store_id: int
    question: str = Field(..., min_length=1, max_length=200)
