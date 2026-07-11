"""static 구현 — LLM 0회.

- StaticTagger: 태그 칩 결정적 매핑 + 자유 텍스트 키워드 규칙(시드 사전 §8)
- StaticClassifier: 질문 키워드 → 토픽
- StaticSynthesizer: f-string 템플릿 (숫자는 전부 서버가 센 값만 주입 — LLM 교체 후에도 동일 원칙)
"""
import re

from ..taxonomy import (ACTION_SUGGESTIONS, CHIP_MAP, DEFAULT_SUGGESTION, INTENSITY_2,
                        INTENSITY_3, KEYWORD_RULES, REORDER_COND, REORDER_NEG, REORDER_POS,
                        TOPICS)


def _clause_around(text: str, keyword: str) -> str:
    """evidence = 키워드가 포함된 절(문장부호 기준) — 원문 부분문자열 보장."""
    idx = text.find(keyword)
    if idx < 0:
        return keyword
    seps = re.split(r"([.!?~\n,]|ㅠㅠ|ㅋㅋ|\.\.)", text)
    pos = 0
    for part in seps:
        if idx < pos + len(part):
            return part.strip() or keyword
        pos += len(part)
    return keyword


def _intensity_of(text: str, base: int = 1) -> int:
    for w in INTENSITY_3:
        if w in text:
            return 3
    for w in INTENSITY_2:
        if w in text:
            return 2
    return base


class StaticTagger:
    def tag(self, text: str, emotion: str, chips: list[str]) -> dict:
        polarity_default = "positive" if emotion == "like" else "negative"
        aspects: list[dict] = []
        seen_topics: set[str] = set()

        # 1) 칩 → 결정적 매핑 (우선)
        for chip in chips:
            if chip not in CHIP_MAP:
                continue
            topic, value, polarity, intensity, scope = CHIP_MAP[chip]
            aspects.append({
                "scope": scope, "topic": topic, "value": value, "polarity": polarity,
                "intensity": intensity, "evidence": None, "normalized": chip, "source": "chip",
            })
            seen_topics.add(topic)

        # 2) 자유 텍스트 키워드 규칙 (칩과 중복 토픽은 건너뜀 — 이중 집계 방지)
        for pattern, topic, value, normalized in KEYWORD_RULES:
            if topic in seen_topics or pattern not in text:
                continue
            clause = _clause_around(text, pattern)
            scope = "menu" if "menu" in TOPICS[topic]["scopes"] else "store"
            aspects.append({
                "scope": scope, "topic": topic, "value": value,
                "polarity": polarity_default,
                "intensity": _intensity_of(clause),
                "evidence": clause, "normalized": normalized, "source": "text",
            })
            seen_topics.add(topic)

        # 3) 아무 태그도 없으면 맛_일반 극성 1건 (총평 수렴처)
        if not aspects:
            aspects.append({
                "scope": "menu", "topic": "맛_일반", "value": None,
                "polarity": polarity_default, "intensity": 1,
                "evidence": text[:50] if text else None,
                "normalized": "좋아하는 맛이에요" if emotion == "like" else "내 취향이 아니에요",
                "source": "text",
            })

        return {"aspects": aspects, "reorder_intent": self._reorder(text)}

    @staticmethod
    def _reorder(text: str) -> str:
        for w in REORDER_NEG:
            if w in text:
                return "neg"
        for w in REORDER_POS:
            if w in text:
                return "pos"
        for w in REORDER_COND:
            if w in text:
                return "conditional"
        return "none"


class StaticClassifier:
    """질문 → 토픽 (닫힌 어휘라 키워드 매칭으로 충분)."""

    RULES = [
        (["맵", "매워", "매운"], "매운맛"),
        (["느끼", "기름"], "느끼함"),
        (["짜", "짭", "싱겁", "간이"], "짠맛"),
        (["달", "당도"], "단맛"),
        (["양 ", "양이", "많아", "혜자", "창렬"], "양"),
        (["식감", "바삭", "쫄깃", "눅눅", "불어"], "식감"),
        (["위생", "깨끗", "지저분", "이물질"], "위생"),
        (["배달", "빨리", "늦"], "배달속도"),
        (["가격", "비싸", "가성비"], "가격"),
        (["포장"], "포장"),
        (["아이랑", "애기", "아이가"], "매운맛"),  # "아이랑 먹기 좋아?" → 맵기 관심사로 해석
        (["서비스", "친절"], "서비스"),
        (["신선"], "신선도"),
    ]

    def classify(self, question: str) -> str:
        for keywords, topic in self.RULES:
            if any(k in question for k in keywords):
                return topic
        return "맛_일반"


class StaticSynthesizer:
    """서버가 계산한 수치만 문장에 끼워넣는다 (표본 수·분포는 절대 생성하지 않음)."""

    # ── 재주문 넛지 (STEP 3-A) ──────────────────────────────────────────
    def nudge(self, ctx: dict) -> str:
        nick = ctx["nickname"]
        neg = ctx.get("latest_negative")          # {menu_name, topic, normalized, direction}
        pos_topics = ctx.get("satisfied_topics", [])
        if neg:
            suggestion = ACTION_SUGGESTIONS.get((neg["topic"], neg["direction"]), DEFAULT_SUGGESTION)
            head = ""
            if pos_topics:
                head = f"{'·'.join(pos_topics[:2])}은(는) 늘 만족하셨는데, "
            menu = f"'{neg['menu_name']}'" if neg.get("menu_name") else "지난 주문"
            return f"{head}{menu}은(는) {neg['normalized']} 기록이 있어요. {suggestion}"
        cond = ctx.get("conditional_note")
        if cond:
            return f"지난번에 \"{cond}\"라고 메모하셨죠. 이번 주문에 반영해보세요!"
        return f"이 가게는 늘 만족하셨어요, {nick}님. 지난번과 같은 구성으로 재주문 어때요?"

    # ── 첫 주문 가게 가이드 (STEP 3-B) ──────────────────────────────────
    def first_guide(self, ctx: dict) -> str:
        nick, topic = ctx["nickname"], ctx["topic"]
        n, m = ctx["n"], ctx["m"]
        months, label = ctx["months"], ctx.get("sample_label")
        if n == 0:
            return "아직 이 가게의 속마음 데이터가 부족해요. 첫 기록의 주인공이 되어주세요!"
        if n <= 2:
            return (f"이 가게의 속마음 데이터가 아직 {n}건뿐이라 정리해 드리기엔 일러요. "
                    f"주문하시면 {nick}님의 기록이 큰 도움이 돼요!")
        tail = f" (최근 {months}개월, 관련 메모 {n}건 기준{'·참고용' if label else ''})"
        my_line = ctx.get("personal_line", "")
        return f"{ctx['stat_line']} {my_line}{tail}"

    # ── 챗봇 답변 (STEP 3-C) ────────────────────────────────────────────
    def chat_answer(self, ctx: dict) -> str:
        topic, n, m = ctx["topic"], ctx["n"], ctx["m"]
        months, source = ctx["months"], ctx["source"]  # source: 이웃 | 전체
        if n == 0:
            return ("아직 이 주제의 속마음 데이터가 없어요. 관련 기록이 쌓이면 "
                    "바로 알려드릴게요!")
        label = "입맛이 비슷한 이용자" if source == "neighbor" else "전체 이용자"
        stat = f"{topic} 관련 메모 {n}건 중 {m}건이 {ctx['m_desc']}."
        my_line = ctx.get("personal_line", "")
        note = "·참고용" if 3 <= n <= 4 else ""
        return f"{stat} {my_line} (최근 {months}개월 · {label} 기록 {n}건 기준{note})"


tagger = StaticTagger()
classifier = StaticClassifier()
synthesizer = StaticSynthesizer()
