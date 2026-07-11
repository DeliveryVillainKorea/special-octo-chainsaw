"""Upstage(solar-pro3) 어댑터 — 챗봇 경로만 LLM, 태깅·넛지는 static 유지.

원칙 유지: 숫자(표본 수·분포)는 전부 서버가 계산해 컨텍스트로 주입하고,
LLM은 문장만 만든다. 모든 호출은 실패 시 static 폴백 (데모 안전장치).
"""
import json
import logging

import httpx

from ..config import LLM_BASE_URL, LLM_MODEL, UPSTAGE_API_KEY
from ..taxonomy import TOPICS
from .static_ai import StaticClassifier, StaticSynthesizer

log = logging.getLogger("sokmaeum.ai")

_static_clf = StaticClassifier()
_static_syn = StaticSynthesizer()

# 테스트에서 transport 주입을 위해 모듈 레벨 클라이언트
_client = httpx.Client(base_url=LLM_BASE_URL, timeout=15.0)


def _chat(messages: list[dict], response_format: dict | None = None,
          temperature: float = 0.3, max_tokens: int = 400) -> str:
    payload = {"model": LLM_MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if response_format:
        payload["response_format"] = response_format
    r = _client.post("/chat/completions",
                     headers={"Authorization": f"Bearer {UPSTAGE_API_KEY}"},
                     json=payload)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ── 질문분류: structured output(json_schema)으로 17종 enum 강제 ──────────────
_TOPIC_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "topic_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"topic": {"type": "string", "enum": list(TOPICS)}},
            "required": ["topic"],
            "additionalProperties": False,
        },
    },
}

_CLASSIFY_SYSTEM = (
    "배달앱 음식 리뷰에 대한 사용자 질문을 아래 17종 주제 중 하나로 분류한다.\n"
    "판단 기준: 질문이 궁금해하는 음식 속성 축. "
    "예) '아이랑 먹어도 될까?'·'맵찔이인데 괜찮아?' → 매운맛, "
    "'기름지지 않아?' → 느끼함, '간이 세?' → 짠맛, '양 많아?' → 양, "
    "'배달 오래 걸려?' → 배달속도, 특정 축이 없으면 → 맛_일반"
)


class UpstageClassifier:
    def classify(self, question: str) -> str:
        try:
            content = _chat(
                [{"role": "system", "content": _CLASSIFY_SYSTEM},
                 {"role": "user", "content": question}],
                response_format=_TOPIC_SCHEMA, temperature=0.0, max_tokens=50)
            topic = json.loads(content)["topic"]
            if topic in TOPICS:
                return topic
            raise ValueError(f"enum 밖 topic: {topic}")
        except Exception as e:
            log.warning("질문분류 LLM 실패 → static 폴백: %s", e)
            return _static_clf.classify(question)


# ── 답변 합성 ────────────────────────────────────────────────────────────────
_ANSWER_SYSTEM = """너는 요기요 '속마음 리뷰'의 AI 비서다. 다른 이용자들의 비공개 메모 통계와 질문자의 입맛 프로필을 근거로, 주문 전 질문에 답한다.

규칙 (위반 금지):
- 컨텍스트 JSON에 있는 숫자만 사용한다. 새로운 숫자·비율·순위·별점을 만들지 않는다.
- 2~3문장, 친근한 존댓말, 단정 대신 "~것 같아요" 톤.
- 첫 문장은 통계 요약: "○○ 관련 메모 N건 중 M건이 …" 형태로 시작.
- 입맛 프로필(profile_axis)이 있으면 질문자 닉네임과 함께 개인화 문장 1개를 넣는다.
- 마지막은 반드시 " (최근 {months}개월 · {source_label} 기록 {n}건 기준{note})" 표기로 끝낸다.
- 다른 가게와의 비교, 가게 총평·추천 순위 언급 금지. 컨텍스트 밖 메뉴 지식 사용 금지."""


class UpstageSynthesizer:
    """챗봇 답변만 LLM. 넛지·첫주문 가이드는 static 템플릿 유지 (튜닝 후 확장)."""

    def nudge(self, ctx: dict) -> str:
        return _static_syn.nudge(ctx)

    def first_guide(self, ctx: dict) -> str:
        return _static_syn.first_guide(ctx)

    def chat_answer(self, ctx: dict) -> str:
        if ctx["n"] == 0:  # 게이트: 데이터 없음 → LLM 호출 없이 고정 응답
            return _static_syn.chat_answer(ctx)
        source_label = "입맛이 비슷한 이용자" if ctx["source"] == "neighbor" else "전체 이용자"
        note = "·참고용" if 3 <= ctx["n"] <= 4 else ""
        payload = {
            "질문": ctx.get("question", ""),
            "가게": ctx.get("store_name", ""),
            "topic": ctx["topic"],
            "통계": {
                "관련 메모 수 N": ctx["n"],
                "만족(긍정) 수 M": ctx["m"],
                "months": ctx["months"],
                "source_label": source_label,
                "note": note,
            },
            "profile_axis": ctx.get("profile_axis"),  # {label, score, tier_name} | null
            "닉네임": ctx.get("nickname", ""),
        }
        try:
            text = _chat(
                [{"role": "system", "content": _ANSWER_SYSTEM},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            ).strip()
            if not text:
                raise ValueError("빈 응답")
            return text
        except Exception as e:
            log.warning("챗 답변 LLM 실패 → static 폴백: %s", e)
            return _static_syn.chat_answer(ctx)
