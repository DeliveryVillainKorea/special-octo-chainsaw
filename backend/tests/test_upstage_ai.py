"""Upstage 어댑터 테스트 — MockTransport로 요청 형태·게이트·폴백 검증 (실 API 호출 없음)."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import upstage_ai
from app.ai.upstage_ai import UpstageClassifier, UpstageSynthesizer


def _mock(handler):
    return httpx.Client(transport=httpx.MockTransport(handler),
                        base_url="https://api.upstage.ai/v1")


def _ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _chat_ctx(n=15, **kw):
    ctx = {"topic": "느끼함", "n": n, "m": 9, "months": 6, "source": "all",
           "m_desc": "만족했어요", "personal_line": "기요님 주문 전에 참고해보세요!",
           "question": "버터갈릭 쉬림프 피자가 너무 느끼하진 않을까?",
           "store_name": "레파레피자-망포역점", "nickname": "기요",
           "profile_axis": {"label": "느끼함", "score": 62, "tier_name": "느끼 즐김"}}
    ctx.update(kw)
    return ctx


def test_classifier_parses_structured_output(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok(json.dumps({"topic": "느끼함"}))

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    assert UpstageClassifier().classify("느끼하진 않을까?") == "느끼함"
    body = captured["body"]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert "느끼함" in body["response_format"]["json_schema"]["schema"]["properties"]["topic"]["enum"]


def test_classifier_falls_back_to_static_on_error(monkeypatch):
    monkeypatch.setattr(upstage_ai, "_client",
                        _mock(lambda req: httpx.Response(500, json={"error": "boom"})))
    assert UpstageClassifier().classify("너무 맵지 않아?") == "매운맛"  # static 규칙 결과


def test_chat_answer_injects_server_numbers(monkeypatch):
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return _ok("느끼함 관련 메모 15건 중 9건이 만족했어요. 기요님께 잘 맞을 것 같아요! "
                   "(최근 6개월 · 전체 이용자 기록 15건 기준)")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageSynthesizer().chat_answer(_chat_ctx())
    assert "15건 중 9건" in out
    user_msg = json.loads(captured["body"]["messages"][1]["content"])
    assert user_msg["통계"]["관련 메모 수 N"] == 15  # 숫자는 서버가 주입
    assert user_msg["profile_axis"]["score"] == 62


def test_chat_answer_gate_n0_skips_llm(monkeypatch):
    def handler(request):
        raise AssertionError("n=0이면 LLM을 호출하면 안 됨")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageSynthesizer().chat_answer(_chat_ctx(n=0, m=0))
    assert "아직" in out  # static 고정 응답


def test_chat_answer_falls_back_on_timeout(monkeypatch):
    def handler(request):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    out = UpstageSynthesizer().chat_answer(_chat_ctx())
    assert "15건 중 9건이 만족했어요" in out  # static 템플릿 폴백


def test_nudge_and_guide_stay_static(monkeypatch):
    def handler(request):
        raise AssertionError("넛지/가이드는 LLM을 호출하면 안 됨 (static 유지)")

    monkeypatch.setattr(upstage_ai, "_client", _mock(handler))
    syn = UpstageSynthesizer()
    assert syn.nudge({"nickname": "기요", "latest_negative": None,
                      "satisfied_topics": [], "conditional_note": None})
    assert syn.first_guide({"nickname": "기요", "topic": "매운맛", "n": 0, "m": 0,
                            "months": 6, "sample_label": None,
                            "stat_line": "", "personal_line": ""})
