"""LLM 어댑터 인터페이스 — static(현재) / upstage(튜닝 후 solar-pro3) 교체 지점.

검증(17종 enum·스코프 화이트리스트·evidence 부분문자열)은 이 인터페이스 바깥
(services.validate_aspects)에서 수행한다 — 구현체가 바뀌어도 같은 관문을 통과.
"""
from typing import Protocol


class Tagger(Protocol):
    def tag(self, text: str, emotion: str, chips: list[str]) -> dict:
        """→ {"aspects": [{scope, topic, value, polarity, intensity, evidence, normalized, source}],
             "reorder_intent": "pos|neg|conditional|none"}"""
        ...


class QuestionClassifier(Protocol):
    def classify(self, question: str) -> str:
        """질문 → 닫힌 어휘 17종 중 topic 1개."""
        ...


class Synthesizer(Protocol):
    def nudge(self, ctx: dict) -> str: ...
    def first_guide(self, ctx: dict) -> str: ...
    def chat_answer(self, ctx: dict) -> str: ...
