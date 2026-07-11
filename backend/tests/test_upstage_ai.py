"""Upstage 어댑터 테스트 — MockTransport (실 API 호출 없음). 설계: docs/LLM_PROMPTS.md"""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import upstage_ai
from app.ai.upstage_ai import UpstageClassifier, UpstageSynthesizer, UpstageTagger


def _mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler),
                        base_url="https://api.upstage.ai/v1")


def _ok(content) -> httpx.Response:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


# ── P2 분류 ──────────────────────────────────────────────────────────────────
def test_classifier_fast_path_skips_llm(monkeypatch):
    def handler(request):
        raise AssertionError("키워드 단일 히트는 LLM을 호출하면 안 됨")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    assert UpstageClassifier().classify("여기 많이 매워?") == "매운맛"


def test_classifier_ambiguous_goes_to_llm(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok({"topic": "맛_일반"})

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    # 규칙 무매치 질문 → LLM 경로
    assert UpstageClassifier().classify("혼밥하기 괜찮아?") == "맛_일반"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    assert captured["body"]["max_tokens"] == 30


def test_classifier_error_falls_back_to_static(monkeypatch):
    monkeypatch.setattr(upstage_ai, "_client",
                        _mock(lambda req: httpx.Response(500, json={"error": "boom"})))
    assert UpstageClassifier().classify("혼밥하기 괜찮아?") == "맛_일반"  # static fallthrough


# ── P3~P5 합성 ───────────────────────────────────────────────────────────────
def test_synthesizer_returns_body_and_none_on_error(monkeypatch):
    monkeypatch.setattr(upstage_ai, "_client", _mock(lambda req: _ok("본문입니다.")))
    assert UpstageSynthesizer().chat_answer({"stat_clause": "x"}) == "본문입니다."

    def boom(request):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(upstage_ai, "_client", _mock(boom))
    assert UpstageSynthesizer().chat_answer({"stat_clause": "x"}) is None  # services가 static 폴백


def test_synthesizer_sends_payload_as_user_json(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok("ok")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    UpstageSynthesizer().nudge({"nickname_call": "기요님", "action_slot": "A!"})
    msgs = captured["body"]["messages"]
    assert msgs[0]["role"] == "system" and "재주문 넛지" in msgs[0]["content"]
    assert json.loads(msgs[1]["content"])["nickname_call"] == "기요님"
    assert captured["body"]["temperature"] == 0.3 and captured["body"]["max_tokens"] == 200


# ── P1 태깅 ──────────────────────────────────────────────────────────────────
_MEMO = "하나도 안 매움 ㅋㅋ 개실망. 담에는 순한맛 말고 시켜야지"


def _tag_response(evidence="하나도 안 매움 ㅋㅋ 개실망"):
    return {"analysis": "반어 — 순함인데 부정.",
            "aspects": [{"scope": "menu", "topic": "매운맛", "value": "순함",
                         "polarity": "negative", "intensity": 3,
                         "evidence": evidence, "normalized": "생각보다 안 매워요"}],
            "self_note": "담에는 순한맛 말고 시켜야지", "reorder_intent": "conditional"}


def test_tagger_merges_chips_and_llm_with_fewshots(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok(_tag_response())

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageTagger().tag(_MEMO, "dislike", ["비싼 것 같아요"], menus=["매운찜닭"], store="찜닭명가")

    msgs = captured["body"]["messages"]
    assert len(msgs) == 1 + 4 * 2 + 1  # system + few-shot 4쌍 + 가변 user
    user_msg = json.loads(msgs[-1]["content"])
    assert user_msg["excluded_topics"] == ["가격"]  # 칩이 처리한 토픽은 제외 지시
    schema = captured["body"]["response_format"]["json_schema"]["schema"]
    assert schema["required"][0] == "analysis"  # 프로퍼티 순서 = 생성 순서 (미니 CoT)

    topics = [a["topic"] for a in out["aspects"]]
    assert topics == ["가격", "매운맛"]  # 칩 + LLM 병합
    assert out["aspects"][0]["source"] == "chip"
    assert out["self_note"] == "담에는 순한맛 말고 시켜야지"
    assert out["reorder_intent"] == "conditional"


def test_tagger_targeted_retry_then_partial_acceptance(monkeypatch):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return _ok(_tag_response(evidence="원문에 없는 문장"))  # 검증 실패 유도
        return _ok(_tag_response())  # 수정본

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageTagger().tag(_MEMO, "dislike", [])
    assert len(calls) == 2
    feedback = calls[1]["messages"][-1]["content"]
    assert "부분문자열이 아님" in feedback and "실패 항목만 수정" in feedback
    assert out["aspects"][0]["evidence"] == "하나도 안 매움 ㅋㅋ 개실망"


def test_tagger_drops_still_bad_aspect_keeps_valid(monkeypatch):
    bad = _tag_response()
    bad["aspects"].append({"scope": "delivery", "topic": "매운맛", "value": "순함",
                           "polarity": "negative", "intensity": 1,
                           "evidence": "하나도 안 매움", "normalized": "x"})  # 조합 불허

    monkeypatch.setattr(upstage_ai, "_client", _mock(lambda req: _ok(bad)))
    out = UpstageTagger().tag(_MEMO, "dislike", [])
    assert len(out["aspects"]) == 1  # 불량 aspect만 폐기 (부분 수용)


def test_tagger_falls_back_to_static_on_failure(monkeypatch):
    def boom(request):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(upstage_ai, "_client", _mock(boom))
    out = UpstageTagger().tag(_MEMO, "dislike", [])
    assert any(a["topic"] == "매운맛" for a in out["aspects"])  # static 규칙이 잡음
    assert out["reorder_intent"] == "conditional"


def test_tagger_skips_llm_when_text_empty(monkeypatch):
    def handler(request):
        raise AssertionError("텍스트 없으면 LLM 호출 금지")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageTagger().tag("", "like", ["양이 넉넉해요"])
    assert out["aspects"][0]["topic"] == "양"
