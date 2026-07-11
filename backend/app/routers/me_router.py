from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import services
from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..taxonomy import AXIS_LABELS

router = APIRouter(tags=["me"])


@router.get("/me/profile")
def my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """입맛 프로필 카드 — 5축 점수/티어/표본 수. n<3 축은 score null = '수집 중'."""
    profile = services.profile_of(db, user.author_hash)
    axes = []
    for key in ["spicy", "salty", "sweet", "greasy", "texture"]:
        e = profile.get(key) or {"score": None, "tier": None, "n": 0}
        axes.append({"key": key, "label": AXIS_LABELS[key], **e,
                     "status": "active" if e.get("score") is not None else "collecting"})
    return {"nickname": user.nickname, "axes": axes,
            "updated_at": profile.get("updated_at")}
