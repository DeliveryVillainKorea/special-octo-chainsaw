"""LLM 어댑터 인터페이스 — static / upstage(solar-pro3) 교체 지점.

컨텍스트(payload)는 services가 조립하고(allowlist 검증), 출력 게이트(꼬리표 서버
부착·숫자 화이트리스트·verbatim·인용 스팬)도 services가 수행한다 — 구현체가
바뀌어도 같은 관문을 통과한다. 상세 설계: docs/LLM_PROMPTS.md
"""
from typing import Protocol


class Tagger(Protocol):
    def tag(self, text: str, emotion: str, chips: list[str],
            menus: list[str] | None = None, store: str | None = None) -> dict:
        """→ {"aspects": [{scope, topic, value, polarity, intensity, evidence, normalized, source}],
             "reorder_intent": "pos|neg|conditional|none",
             "self_note": "원문 부분문자열 다짐 구절 | ''"}"""
        ...


class QuestionClassifier(Protocol):
    def classify(self, question: str) -> str:
        """질문 → 닫힌 어휘 17종 중 topic 1개."""
        ...


class Synthesizer(Protocol):
    """본문만 생성 (꼬리표 없음). upstage 구현은 실패 시 None → services가 static 폴백."""

    def nudge(self, payload: dict) -> str | None: ...
    def first_guide(self, payload: dict) -> str | None: ...
    def chat_answer(self, payload: dict) -> str | None: ...
