"""출력 게이트·검증 관문 테스트 — docs/LLM_PROMPTS.md §1 게이트 5종."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import (_assert_payload, _stat_clause, apply_output_gates, render_footer,
                          validate_aspects)
from app.ai.static_ai import StaticSynthesizer, StaticTagger


def _p3_payload(**kw):
    p = {"question": "많이 매워?", "store_name": "신전떡볶이", "topic": "매운맛",
         "stat_clause": "매운맛 관련 메모 12건 중 7건이 '생각보다 맵다'는 취지였어요.",
         "own_memos": [{"when_label": "지난달", "menu": "로제 떡볶이",
                        "quote": "보통맛인데도 코끝이 찡함"}],
         "self_note": None,
         "profile_axis": {"label": "매운맛", "score": 23, "tier_name": "맵찔이"},
         "nickname_call": "기요님",
         "action_slot": "이번엔 순한맛(1단계) 옵션을 고려해보세요!"}
    p.update(kw)
    return p


GOOD_BODY = ("매운맛 관련 메모 12건 중 7건이 '생각보다 맵다'는 취지였어요. "
             "기요님도 지난달 로제 떡볶이에 '보통맛인데도 코끝이 찡함'이라고 남기셨죠. "
             "평소 맵찔이시라면, 이번엔 순한맛(1단계) 옵션을 고려해보세요!")


def test_good_body_passes_gates():
    assert apply_output_gates(GOOD_BODY, _p3_payload()) == GOOD_BODY


def test_footer_written_by_llm_is_stripped():
    body = GOOD_BODY + " (최근 6개월 · 전체 이용자 기록 12건 기준)"
    assert apply_output_gates(body, _p3_payload()) == GOOD_BODY  # 서버가 다시 붙인다


def test_hallucinated_number_rejected():
    body = GOOD_BODY.replace("12건 중 7건", "12건 중 7건") + " 무려 95%가 추천했어요."
    assert apply_output_gates(body, _p3_payload()) is None


def test_profile_score_must_not_be_spoken():
    body = GOOD_BODY + " 회원님 매운맛 점수는 23점이에요."
    assert apply_output_gates(body, _p3_payload()) is None  # score는 허용 숫자에서 제외


def test_stat_clause_verbatim_required():
    body = "매운맛 메모 12건 중 7건이 맵다고 했어요. " + \
        "이번엔 순한맛(1단계) 옵션을 고려해보세요!"
    assert apply_output_gates(body, _p3_payload()) is None  # 절 변형 → 폴백


def test_action_slot_verbatim_required():
    body = GOOD_BODY.replace("이번엔 순한맛(1단계) 옵션을 고려해보세요!",
                             "순한맛을 드셔보세요.")
    assert apply_output_gates(body, _p3_payload()) is None


def test_unsourced_quote_rejected():
    body = GOOD_BODY + " 다른 분은 '평생 최고의 맛집'이라고 했어요."
    assert apply_output_gates(body, _p3_payload()) is None  # 근거 없는 인용


def test_allowlist_blocks_unknown_keys():
    with pytest.raises(ValueError):
        _assert_payload("P3", _p3_payload(other_user_memo="타인 원문"))


def test_footer_format_is_canonical():
    assert render_footer(12, "전체 이용자 기록", "·참고용") == \
        " (최근 6개월 · 전체 이용자 기록 12건 기준·참고용)"


def test_stat_clause_framing_by_axis_kind():
    axis = _stat_clause("매운맛", {"n": 12, "m_high": 7, "m_pos": 5, "kind": "axis",
                                 "high_label": "맵다", "months": 6})
    pol = _stat_clause("가격", {"n": 9, "m_high": 0, "m_pos": 6, "kind": "polarity",
                               "high_label": None, "months": 6})
    assert "'생각보다 맵다'는 취지" in axis and "12건 중 7건" in axis
    assert "만족했어요" in pol and "9건 중 6건" in pol


# ── 검증 관문 (validate_aspects) ────────────────────────────────────────────
def test_evidence_fuzzy_snap_to_real_span():
    text = "국물이 좀 짰음. 담엔 맵기 1단계로"
    raw = {"aspects": [{"scope": "menu", "topic": "짠맛", "value": "짜다",
                        "polarity": "negative", "intensity": 2,
                        "evidence": "국물이 좀짰음", "normalized": "짜요"}],  # 띄어쓰기 변형
           "reorder_intent": "conditional", "self_note": "담엔 맵기 1단계로"}
    aspects, reorder, self_note = validate_aspects(raw, text)
    assert aspects[0]["evidence"] is not None
    assert aspects[0]["evidence"] in text  # 저장 값은 항상 참 원문 스팬
    assert self_note == "담엔 맵기 1단계로"


def test_self_note_must_be_substring():
    raw = {"aspects": [], "reorder_intent": "conditional", "self_note": "지어낸 다짐"}
    _, _, self_note = validate_aspects(raw, "다음엔 순한맛으로 시켜야지")
    assert self_note == ""


# ── static 구현 스모크 (LLM payload와 같은 형태 소비, 꼬리표 없음) ───────────
def test_static_tagger_extracts_self_note():
    out = StaticTagger().tag("고바삭이 맛도리임, 담에 시킬 땐 꼭 소스 추가ㄱㄱ", "like", [])
    assert out["reorder_intent"] == "conditional"
    assert out["self_note"] == "담에 시킬 땐 꼭 소스 추가ㄱㄱ"


def test_static_bodies_have_no_footer():
    syn = StaticSynthesizer()
    chat = syn.chat_answer(_p3_payload())
    assert "(최근" not in chat and chat.startswith("매운맛 관련 메모 12건")
    nudge = syn.nudge({"nickname_call": "기요님", "praise_topics": ["매운맛"],
                       "latest_negative": {"when_label": "지난달", "menu": "고구마 피자",
                                           "quote": "양파가 아쉬움"},
                       "self_note": None, "profile_axis": None,
                       "action_slot": "이번엔 양파 토핑을 고려해보세요!"})
    assert "'고구마 피자'" in nudge and nudge.endswith("이번엔 양파 토핑을 고려해보세요!")
