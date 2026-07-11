"""static 구현 — LLM 0회. (프롬프트 설계 v2: docs/LLM_PROMPTS.md)

- StaticTagger: 태그 칩 결정적 매핑 + 자유 텍스트 키워드 규칙 + self_note 추출
- StaticClassifier: 질문 키워드 → 토픽 (upstage 모드에서도 fast-path로 재사용)
- StaticSynthesizer: LLM 페이로드와 **같은 형태**를 소비해 본문만 렌더
  (꼬리표는 services.render_footer가 부착 — static/LLM 폴백이 바이트 단위로 안 보이게)
"""
import re

from ..taxonomy import (CHIP_MAP, INTENSITY_2, INTENSITY_3, KEYWORD_RULES, REORDER_COND,
                        REORDER_NEG, REORDER_POS, TOPICS)


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


def chips_to_aspects(chips: list[str]) -> list[dict]:
    """칩 → aspect 결정적 매핑 (서버 소유 — LLM 태거 모드에서도 이 함수가 처리)."""
    out = []
    for chip in chips:
        if chip not in CHIP_MAP:
            continue
        topic, value, polarity, intensity, scope = CHIP_MAP[chip]
        out.append({"scope": scope, "topic": topic, "value": value, "polarity": polarity,
                    "intensity": intensity, "evidence": None, "normalized": chip,
                    "source": "chip"})
    return out


def extract_reorder(text: str) -> str:
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


def extract_self_note(text: str) -> str:
    """'다음엔 이렇게 해야지' 다짐 구절 — 원문 절 그대로 (부분문자열 보장)."""
    for w in REORDER_COND:
        if w in text:
            return _clause_around(text, w)[:120]
    return ""


class StaticTagger:
    def tag(self, text: str, emotion: str, chips: list[str],
            menus: list[str] | None = None, store: str | None = None) -> dict:
        polarity_default = "positive" if emotion == "like" else "negative"
        aspects = chips_to_aspects(chips)
        seen_topics = {a["topic"] for a in aspects}

        # 자유 텍스트 키워드 규칙 (칩과 중복 토픽은 건너뜀 — 이중 집계 방지)
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

        if not aspects:  # 아무 태그도 없으면 맛_일반 극성 1건 (총평 수렴처)
            aspects.append({
                "scope": "menu", "topic": "맛_일반", "value": None,
                "polarity": polarity_default, "intensity": 1,
                "evidence": text[:50] if text else None,
                "normalized": "좋아하는 맛이에요" if emotion == "like" else "내 취향이 아니에요",
                "source": "text",
            })

        return {"aspects": aspects, "reorder_intent": extract_reorder(text),
                "self_note": extract_self_note(text)}


class StaticClassifier:
    """질문 → 토픽. upstage 모드에서도 단일 키워드 히트는 이 규칙이 fast-path로 처리."""

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
        (["아이랑", "애기", "아이가"], "매운맛"),  # "아이랑 먹기 좋아?" → 맵기 관심사
        (["서비스", "친절"], "서비스"),
        (["신선"], "신선도"),
    ]

    def keyword_hits(self, question: str) -> set[str]:
        return {t for kws, t in self.RULES if any(k in question for k in kws)}

    def classify(self, question: str) -> str:
        for keywords, topic in self.RULES:
            if any(k in question for k in keywords):
                return topic
        return "맛_일반"


# ── 본문 렌더러 (payload 형태는 LLM과 동일 — docs/LLM_PROMPTS.md §P3~P5) ─────
def _tier_line(p: dict) -> str:
    ax = p.get("profile_axis")
    nick = p.get("nickname_call", "회원님")
    if ax and ax.get("tier_name"):
        return f"평소 {ax['tier_name']}이신 {nick}에게 참고가 될 것 같아요."
    return f"{nick} 주문 전에 참고해보세요!"


class StaticSynthesizer:
    """서버가 조립한 stat_clause·action_slot을 그대로 엮는다. 꼬리표는 안 쓴다(서버 부착)."""

    def chat_answer(self, p: dict) -> str:
        parts = [p["stat_clause"]]
        own = p.get("own_memos") or []
        if own:
            m0 = own[0]
            parts.append(f"{p['nickname_call']}도 {m0['when_label']} {m0['menu']}에 "
                         f"'{m0['quote']}'라고 남기셨죠.")
        else:
            parts.append(_tier_line(p))
        if p.get("action_slot"):
            parts.append(p["action_slot"])
        return " ".join(parts)

    def nudge(self, p: dict) -> str:
        sn = p.get("self_note")
        if sn:
            return (f"지난번에 '{sn['quote']}'라고 남기셨죠. {p['action_slot']}")
        ln = p.get("latest_negative")
        if ln:
            head = ""
            praise = p.get("praise_topics") or []
            if praise:
                head = f"{'·'.join(praise[:2])}은(는) 늘 만족하셨는데, "
            return (f"{head}{ln['when_label']} '{ln['menu']}'는 '{ln['quote']}' 기록이 있어요. "
                    f"{p['action_slot']}")
        return f"이 가게는 늘 만족하셨어요, {p['nickname_call']}. 같은 구성으로 재주문 어때요?"

    def first_guide(self, p: dict) -> str:
        parts = [p["stat_clause"]]
        ax, nick = p.get("profile_axis"), p.get("nickname_call", "회원님")
        if p.get("fit_hint") == "충돌" and ax:
            parts.append(f"평소 {ax['tier_name']}이신 {nick}에게는 살짝 벅찰 수 있어요.")
        elif p.get("fit_hint") == "잘맞음" and ax:
            parts.append(f"{ax['tier_name']}이신 {nick} 입맛에는 오히려 반가운 소식이에요.")
        else:
            parts.append(_tier_line(p))
        if p.get("action_slot"):
            parts.append(p["action_slot"])
        return " ".join(parts)


tagger = StaticTagger()
classifier = StaticClassifier()
synthesizer = StaticSynthesizer()
