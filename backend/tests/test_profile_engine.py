"""프로필 엔진 단위 테스트 — 입맛프로필_산정체계.html의 계산 예시가 곧 픽스처."""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import utcnow
from app.profile_engine import (axis_score, build_profile, find_neighbors, similarity,
                                target_of, texture_score, tier_of)


def _asp(value, polarity, intensity=1, days_ago=0, normalized="", ref_id=None):
    return {"value": value, "polarity": polarity, "intensity": intensity,
            "normalized": normalized, "created_at": utcnow() - timedelta(days=days_ago),
            "ref_id": ref_id}


# ── 문서 §4: 메모 3건이 48점으로 수렴 ────────────────────────────────────────
def test_doc_example_three_memos_converge_to_48():
    aspects = [
        _asp("맵다", "negative", 3, ref_id=1),   # "불닭 너무 매워서 반 남김" → 75−30=45
        _asp("보통", "positive", 1, ref_id=2),   # "신전 보통맛 딱 좋았음"   → 50
        _asp("순함", "negative", 2, ref_id=3),   # "하나도 안 매움 ㅋㅋ 아쉽" → 25+20=45
    ]
    out = axis_score("매운맛", aspects)
    assert out["score"] == 48          # (50×2 + 45+50+45) / 5
    assert out["tier"] == 2            # 40~59 구간
    assert out["n"] == 3
    assert out["evidence_ids"] == [1, 2, 3]


def test_target_directions():
    assert target_of("매운맛", "맵다", "positive", 3) == 75      # 긍정 → S 그대로
    assert target_of("매운맛", "맵다", "negative", 3) == 45      # 과함 → 내려잡음
    assert target_of("매운맛", "순함", "negative", 2) == 45      # 부족 → 올려잡음
    assert target_of("매운맛", "보통", "neutral", 1) is None     # 중립 → 침묵
    assert target_of("매운맛", None, "positive", 1) == 50        # 값 없음 → 50


def test_boundary_neg_at_50_uses_normalized_direction():
    assert target_of("매운맛", "보통", "negative", 1, "보통인데도 맵다고 느낌") == 40
    assert target_of("매운맛", "보통", "negative", 1, "보통인데 순한 느낌이라 아쉬움") == 60
    assert target_of("매운맛", "보통", "negative", 1, "그냥 별로") is None  # 판정 불가 → 제외


def test_under_three_memos_is_collecting():
    out = axis_score("매운맛", [_asp("맵다", "positive"), _asp("맵다", "positive")])
    assert out["score"] is None and out["n"] == 2  # "수집 중"


def test_recency_halflife_pulls_toward_recent():
    old_spicy_fan = [_asp("맵다", "positive", days_ago=360)] * 2 + [_asp("맵다", "positive", days_ago=300)]
    recent_averse = old_spicy_fan + [_asp("맵다", "negative", 3, days_ago=0)] * 3
    high = axis_score("매운맛", old_spicy_fan)["score"]
    moved = axis_score("매운맛", recent_averse)["score"]
    assert moved < high  # 최근 부정 기록이 점수를 끌어내림


def test_tier_boundaries():
    assert tier_of(0) == 0 and tier_of(19.9) == 0
    assert tier_of(48) == 2
    assert tier_of(100) == 4  # 100점은 마지막 구간에 흡수


def test_texture_sensitivity_and_chips():
    tex = [_asp(None, "positive", 1, normalized="바삭"),
           _asp(None, "positive", 2, normalized="바삭"),
           _asp(None, "negative", 2, normalized="눅눅")]
    out = texture_score(tex, total_memos=9)  # 언급률 1/3 → 빈도항 만점
    assert out["score"] == round(100 * (0.6 * 1.0 + 0.4 * (5 / 3) / 3))
    assert out["likes"] == ["바삭"] and out["dislikes"] == ["눅눅"]


def test_similarity_needs_three_shared_active_axes():
    p = build_profile({"매운맛": [_asp("맵다", "positive")] * 3}, total_memos=3)
    q = build_profile({"매운맛": [_asp("맵다", "positive")] * 3}, total_memos=3)
    assert similarity(p, q) is None  # 활성 축 1개뿐 → 정의 안 됨


def test_neighbors_top_k_and_threshold():
    base = {"매운맛": [_asp("맵다", "positive")] * 3,
            "짠맛": [_asp("짜다", "positive")] * 3,
            "단맛": [_asp("달다", "positive")] * 3}
    me = build_profile(base, total_memos=9)
    same = build_profile(base, total_memos=9)
    opposite = build_profile({"매운맛": [_asp("순함", "positive")] * 6,
                              "짠맛": [_asp("싱겁다", "positive")] * 6,
                              "단맛": [_asp("안달다", "positive")] * 6}, total_memos=18)
    found = find_neighbors(me, {"twin": same, "opp": opposite})
    assert [h for h, _ in found] == ["twin"]
    assert found[0][1] == 1.0
