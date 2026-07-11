"""도메인 서비스 — 검증 관문 / 이중 저장 / 프로필 재계산 / 집계·게이트 / 가이드·챗 빌더.

숫자·프레이밍·행동제안·꼬리표는 전부 여기(서버)서 만든다. AI 계층(static/upstage)은
서버가 조립한 문장 재료를 엮기만 한다. 상세 설계: docs/LLM_PROMPTS.md
"""
import difflib
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai import AI_MODE, classifier, synthesizer, tagger
from .ai.static_ai import synthesizer as static_synthesizer
from .config import STATS_WINDOW_MONTHS, utcnow
from .models import AnonAspect, Menu, MemoAspect, PersonalMemo, Store, TasteProfile, User
from .profile_engine import ALL_AXES, build_profile, find_neighbors
from .taxonomy import (ACTION_SUGGESTIONS, AXIS_LABELS, DEFAULT_SUGGESTION, POLARITIES,
                       PROFILE_AXES, TOPICS, is_valid_combo)

AXIS_BY_TOPIC = {v: k for k, v in PROFILE_AXES.items()}  # "매운맛" -> "spicy"

# 프롬프트별 컨텍스트 키 allowlist — "타인 원문 미주입"을 런타임 불변식으로 (게이트 #5)
PROMPT_ALLOWED_KEYS: dict[str, set[str]] = {
    "P1": {"memo", "emotion", "excluded_topics", "menus", "store"},
    "P2": {"question"},
    "P3": {"question", "store_name", "topic", "stat_clause", "profile_axis",
           "nickname_call", "own_memos", "self_note", "action_slot"},
    "P4": {"nickname_call", "praise_topics", "latest_negative", "self_note",
           "profile_axis", "action_slot"},
    "P5": {"nickname_call", "topic", "stat_clause", "profile_axis",
           "fit_hint", "action_slot"},
}

_FOOTER_RE = re.compile(r"\s*[\(（][^\(\)（）]*(?:최근|기준|기록|개월|참고용|본인)[^\(\)（）]*[\)）]\s*$")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_QUOTE_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"|“([^”]+)”|‘([^’]+)’|「([^」]+)」")

SELF_NOTE_ACTION = "이번 주문에 바로 반영해보세요!"


# ── 검증 관문 (모델 무관 — static이든 LLM이든 같은 문을 통과) ────────────────
def _snap_evidence(evidence: str | None, text: str) -> str | None:
    """evidence 부분문자열 검증 + 퍼지 리페어 — 저장되는 값은 항상 참 원문 스팬(≤60자)."""
    if not evidence:
        return None
    if evidence in text:
        return evidence[:60]
    # 리페어 1: 공백 정규화 재탐색 → 원문 스팬으로 역매핑
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    text_ns = "".join(text[i] for i in positions)
    ev_ns = "".join(ch for ch in evidence if not ch.isspace())
    if ev_ns:
        idx = text_ns.find(ev_ns)
        if idx >= 0:
            start, end = positions[idx], positions[idx + len(ev_ns) - 1] + 1
            return text[start:end][:60]
    # 리페어 2: difflib 최장 일치 블록 (유사도 ≥0.9)
    sm = difflib.SequenceMatcher(None, text, evidence)
    m = sm.find_longest_match(0, len(text), 0, len(evidence))
    if m.size and m.size / len(evidence) >= 0.9:
        return text[m.a:m.a + m.size][:60]  # 실제 원문 스팬으로 스냅
    return None


def validate_aspects(raw: dict, text: str) -> tuple[list[dict], str, str]:
    valid: list[dict] = []
    for a in raw.get("aspects", []):
        topic, scope = a.get("topic"), a.get("scope")
        if topic not in TOPICS or not is_valid_combo(scope, topic):
            continue  # 스코프×태그 화이트리스트 위반 → 거부
        if a.get("polarity") not in POLARITIES:
            continue
        axis = TOPICS[topic]["axis"]
        value = a.get("value")
        if axis is None:
            value = None
        elif value is not None and value not in axis:
            value = None  # 축 밖 값 → 값 없이 극성만 반영
        valid.append({
            "scope": scope, "topic": topic, "value": value,
            "polarity": a["polarity"],
            "intensity": max(1, min(3, int(a.get("intensity", 1)))),
            "evidence": _snap_evidence(a.get("evidence"), text),
            "normalized": (a.get("normalized") or "")[:100],
            "source": a.get("source", "text"),
        })
    reorder = raw.get("reorder_intent", "none")
    if reorder not in {"pos", "neg", "conditional", "none"}:
        reorder = "none"
    self_note = (raw.get("self_note") or "")[:120]
    if self_note and self_note not in text:  # evidence와 동일한 부분문자열 규칙
        self_note = ""
    return valid, reorder, self_note


# ── 출력 게이트 (게이트 #1~#4) ───────────────────────────────────────────────
def render_footer(n: int, source_label: str, note: str = "",
                  months: int = STATS_WINDOW_MONTHS) -> str:
    """꼬리표는 LLM 금지 — 서버가 canonical 형태로 부착 (static/LLM 바이트 동일)."""
    return f" (최근 {months}개월 · {source_label} {n}건 기준{note})"


def _allowed_text(payload: dict) -> str:
    """payload의 문자열 전부 (profile_axis.score 제외 — 점수 발화 금지 집행)."""
    parts: list[str] = []

    def walk(obj, skip_score=False):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if skip_score and k == "score":
                    continue
                walk(v, skip_score=skip_score or k == "profile_axis")
        elif isinstance(obj, list):
            for v in obj:
                walk(v, skip_score)
        elif isinstance(obj, str):
            parts.append(obj)

    walk(payload)
    return " \n ".join(parts)


def apply_output_gates(body: str | None, payload: dict) -> str | None:
    """LLM/static 본문 공통 게이트. 실패 시 None → 호출측이 static 폴백."""
    if not body or not body.strip():
        return None
    text = body.strip()
    for _ in range(2):  # 꼬리표를 직접 썼다면 제거 (서버가 다시 붙인다)
        stripped = _FOOTER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped.strip()
    allowed = _allowed_text(payload)
    allowed_nums = set(_NUMBER_RE.findall(allowed))
    if any(num not in allowed_nums for num in _NUMBER_RE.findall(text)):
        return None  # 환각 숫자 (게이트 #2)
    for k, v in payload.items():
        if isinstance(v, str) and v and (k.endswith("_clause") or k.endswith("_slot")):
            if v not in text:
                return None  # verbatim 위반 (게이트 #3)
            if text.count(v) > 1:
                return None  # 절 반복 퇴화 (재료가 없을 때 같은 문장 반복 방지)
    for m in _QUOTE_RE.finditer(text):
        span = next(g for g in m.groups() if g)
        if len(span) >= 4 and span not in allowed:
            return None  # 근거 없는 인용 (게이트 #4)
    return text


def _assert_payload(prompt_id: str, payload: dict) -> dict:
    extra = set(payload) - PROMPT_ALLOWED_KEYS[prompt_id]
    if extra:
        raise ValueError(f"{prompt_id} 컨텍스트 allowlist 위반: {extra}")
    return payload


def _synthesize(prompt_id: str, method: str, payload: dict) -> str:
    """LLM 시도 → 게이트 → static 폴백 → 게이트 → 최후엔 static 원문."""
    _assert_payload(prompt_id, payload)
    body = apply_output_gates(getattr(synthesizer, method)(payload), payload)
    if body is None:
        static_body = getattr(static_synthesizer, method)(payload)
        body = apply_output_gates(static_body, payload) or static_body
    return body


# ── 프로필 재계산 ────────────────────────────────────────────────────────────
def _upsert_profile(db: Session, author_hash: str, data: dict) -> dict:
    row = db.scalar(select(TasteProfile).where(TasteProfile.author_hash == author_hash))
    if row is None:
        row = TasteProfile(author_hash=author_hash, data=data, updated_at=utcnow())
        db.add(row)
    else:
        row.data = data
        row.updated_at = utcnow()
    return data


def recompute_user_profile(db: Session, user: User) -> dict:
    rows = db.execute(
        select(MemoAspect, PersonalMemo.created_at, PersonalMemo.id)
        .join(PersonalMemo, MemoAspect.memo_id == PersonalMemo.id)
        .where(PersonalMemo.user_id == user.id)
    ).all()
    by_topic: dict[str, list[dict]] = {}
    for asp, created_at, memo_id in rows:
        by_topic.setdefault(asp.topic, []).append({
            "value": asp.value, "polarity": asp.polarity, "intensity": asp.intensity,
            "normalized": asp.normalized, "created_at": created_at, "ref_id": memo_id,
        })
    total = db.scalar(select(func.count(PersonalMemo.id)).where(PersonalMemo.user_id == user.id)) or 0
    return _upsert_profile(db, user.author_hash, build_profile(by_topic, total))


def recompute_hash_profile(db: Session, author_hash: str) -> dict:
    rows = db.scalars(select(AnonAspect).where(AnonAspect.author_hash == author_hash)).all()
    by_topic: dict[str, list[dict]] = {}
    groups = set()
    for asp in rows:
        groups.add(asp.memo_group)
        by_topic.setdefault(asp.topic, []).append({
            "value": asp.value, "polarity": asp.polarity, "intensity": asp.intensity,
            "normalized": asp.normalized, "created_at": asp.created_at, "ref_id": None,
        })
    return _upsert_profile(db, author_hash, build_profile(by_topic, len(groups)))


def profile_of(db: Session, author_hash: str) -> dict:
    row = db.scalar(select(TasteProfile).where(TasteProfile.author_hash == author_hash))
    return row.data if row else {}


def profile_delta(old: dict, new: dict) -> dict:
    delta = {}
    for ax in ALL_AXES:
        o, n = old.get(ax) or {}, new.get(ax) or {}
        if o.get("score") != n.get("score"):
            delta[ax] = {"before": o.get("score"), "after": n.get("score"),
                         "before_tier": o.get("tier"), "after_tier": n.get("tier")}
    return delta


# ── 메모 저장 (이중 저장 + 프로필 트리거) ───────────────────────────────────
def create_memo(db: Session, user: User, store: Store, menu_ids: list[int],
                emotion: str, text: str, chips: list[str]) -> dict:
    old_profile = profile_of(db, user.author_hash)
    menu_names = [m.name for m in db.scalars(select(Menu).where(Menu.id.in_(menu_ids))).all()] \
        if menu_ids else []
    raw = tagger.tag(text, emotion, chips, menus=menu_names, store=store.name)
    aspects, reorder, self_note = validate_aspects(raw, text)

    memo = PersonalMemo(user_id=user.id, store_id=store.id, menu_ids=menu_ids,
                        emotion=emotion, text=text, chips=chips,
                        reorder_intent=reorder, self_note=self_note, created_at=utcnow())
    db.add(memo)
    db.flush()
    first_menu = menu_ids[0] if menu_ids else None
    for a in aspects:
        db.add(MemoAspect(memo_id=memo.id, **a))
        db.add(AnonAspect(author_hash=user.author_hash, store_id=store.id, menu_id=first_menu,
                          memo_group=f"m{memo.id}", scope=a["scope"], topic=a["topic"],
                          value=a["value"], polarity=a["polarity"], intensity=a["intensity"],
                          normalized=a["normalized"], created_at=memo.created_at))

    db.flush()  # autoflush=False — 재계산 SELECT가 새 aspect를 보도록 명시 flush
    new_profile = recompute_user_profile(db, user)
    db.commit()
    return {"memo_id": memo.id, "aspects": aspects, "reorder_intent": reorder,
            "self_note": self_note,
            "profile_delta": profile_delta(old_profile, new_profile), "profile": new_profile}


# ── 집계 (표본 수는 서버가 센다) ────────────────────────────────────────────
def store_topic_stats(db: Session, store_id: int, topic: str,
                      author_hashes: list[str] | None = None) -> dict:
    cutoff = utcnow() - timedelta(days=30 * STATS_WINDOW_MONTHS)
    q = select(AnonAspect).where(AnonAspect.store_id == store_id,
                                 AnonAspect.topic == topic,
                                 AnonAspect.created_at >= cutoff)
    if author_hashes is not None:
        if not author_hashes:
            return {"n": 0, "m_high": 0, "m_pos": 0, "kind": "polarity",
                    "high_label": None, "months": STATS_WINDOW_MONTHS}
        q = q.where(AnonAspect.author_hash.in_(author_hashes))
    rows = db.scalars(q).all()
    axis = TOPICS[topic]["axis"]
    n = len(rows)
    high = axis[2] if axis else None
    return {
        "n": n,
        # 값 축 프레이밍("생각보다 맵다" 취지 건수) — 서버가 절 조립 시 선택
        "m_high": sum(1 for r in rows if axis and r.value == high),
        # 만족 프레이밍(긍정 극성 건수)
        "m_pos": sum(1 for r in rows if r.polarity == "positive"),
        "kind": "axis" if axis else "polarity", "high_label": high,
        "months": STATS_WINDOW_MONTHS,
    }


def _stat_clause(topic: str, stats: dict) -> str:
    """m_pos/m_high 프레이밍은 서버가 확정 — 모델이 보는 유일한 숫자 출처 (원칙 #2·#3)."""
    if stats["kind"] == "axis":
        return f"{topic} 관련 메모 {stats['n']}건 중 {stats['m_high']}건이 '생각보다 {stats['high_label']}'는 취지였어요."
    return f"{topic} 관련 메모 {stats['n']}건 중 {stats['m_pos']}건이 만족했어요."


# ── 페이로드 재료 빌더 ───────────────────────────────────────────────────────
def _when_label(dt) -> str:
    days = max((utcnow() - dt).days, 0)
    if days == 0:
        return "오늘"
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    if days < 14:
        return "지난주"
    if days < 60:
        return "지난달"
    return f"{days // 30}개월 전"


def _first_menu_name(db: Session, memo: PersonalMemo) -> str:
    names = [m.name for m in db.scalars(select(Menu).where(Menu.id.in_(memo.menu_ids or []))).all()]
    return names[0] if names else "지난 주문"


def _profile_axis_payload(profile: dict, topic: str) -> dict | None:
    axis_key = AXIS_BY_TOPIC.get(topic) or ("texture" if topic == "식감" else None)
    if not axis_key:
        return None
    e = profile.get(axis_key) or {}
    if e.get("score") is None:
        return None
    return {"label": AXIS_LABELS[axis_key], "score": e["score"], "tier_name": e.get("tier_name")}


def _own_memos_payload(db: Session, user: User, store: Store, limit: int = 2) -> list[dict]:
    """재료 A — 본인 메모만, 이 가게만, 최근 ≤2건, 인용문 30자 캡 (채널 분리 원칙)."""
    memos = db.scalars(
        select(PersonalMemo).where(PersonalMemo.user_id == user.id,
                                   PersonalMemo.store_id == store.id)
        .order_by(PersonalMemo.created_at.desc()).limit(limit)
    ).all()
    return [{"when_label": _when_label(m.created_at),
             "menu": _first_menu_name(db, m),
             "quote": m.text[:30]} for m in memos if m.text]


def _self_note_payload(db: Session, user: User, store: Store) -> dict | None:
    memo = db.scalar(
        select(PersonalMemo).where(PersonalMemo.user_id == user.id,
                                   PersonalMemo.store_id == store.id,
                                   PersonalMemo.self_note != "")
        .order_by(PersonalMemo.created_at.desc())
    )
    if memo is None:
        return None
    return {"when_label": _when_label(memo.created_at),
            "menu": _first_menu_name(db, memo), "quote": memo.self_note[:40]}


# ── 가이드 카드 (STEP 3-A 재주문 넛지 / 3-B 첫 주문 가이드) ─────────────────
def _memo_badge(db: Session, memo: PersonalMemo) -> dict:
    names = [m.name for m in db.scalars(select(Menu).where(Menu.id.in_(memo.menu_ids or []))).all()]
    label = names[0] + (f" 외 {len(names) - 1}개" if len(names) > 1 else "") if names else ""
    return {"memo_id": memo.id, "emotion": memo.emotion, "text": memo.text,
            "menu_label": label, "chips": memo.chips,
            "reorder_intent": memo.reorder_intent, "created_at": memo.created_at.date().isoformat()}


def build_guide(db: Session, user: User, store: Store) -> dict:
    my_memos = db.scalars(
        select(PersonalMemo).where(PersonalMemo.user_id == user.id,
                                   PersonalMemo.store_id == store.id)
        .order_by(PersonalMemo.created_at.desc())
    ).all()
    profile = profile_of(db, user.author_hash)
    nickname_call = f"{user.nickname}님"

    if my_memos:  # ── P4 재주문 넛지 (100% 본인 데이터 카드 — 타인 통계 주입 금지) ──
        aspects = db.execute(
            select(MemoAspect, PersonalMemo)
            .join(PersonalMemo, MemoAspect.memo_id == PersonalMemo.id)
            .where(PersonalMemo.user_id == user.id, PersonalMemo.store_id == store.id)
            .order_by(PersonalMemo.created_at.desc())
        ).all()
        satisfied, latest_neg, neg_topic, neg_dir = [], None, None, "neg"
        for asp, memo in aspects:
            if asp.polarity == "positive" and asp.topic not in satisfied:
                satisfied.append(asp.topic)
            if latest_neg is None and asp.polarity == "negative":
                axis = TOPICS[asp.topic]["axis"]
                if axis and asp.value == axis[2]:
                    neg_dir = "over"
                elif axis and asp.value == axis[0]:
                    neg_dir = "under"
                neg_topic = asp.topic
                latest_neg = {"when_label": _when_label(memo.created_at),
                              "menu": _first_menu_name(db, memo),
                              "quote": (asp.evidence or asp.normalized or "아쉬움")[:40]}
        self_note = _self_note_payload(db, user, store)
        if self_note is None and latest_neg is None:  # 전부 만족 → LLM 불필요 (사전 게이트)
            message = f"이 가게는 늘 만족하셨어요, {nickname_call}. 같은 구성으로 재주문 어때요?"
        else:
            action = SELF_NOTE_ACTION if self_note else \
                ACTION_SUGGESTIONS.get((neg_topic, neg_dir), DEFAULT_SUGGESTION)
            payload = {
                "nickname_call": nickname_call,
                "praise_topics": satisfied[:2],
                "latest_negative": latest_neg,
                "self_note": self_note,
                "profile_axis": _profile_axis_payload(profile, neg_topic) if neg_topic else None,
                "action_slot": action,
            }
            message = _synthesize("P4", "nudge", payload)
        return {"type": "renudge", "title": "AI 재주문 가이드", "message": message,
                "evidence_memos": [_memo_badge(db, m) for m in my_memos[:5]],
                "ai_mode": AI_MODE}

    # ── P5 첫 주문 가이드 (집단 속마음 × 내 프로필 대조) ─────────────────────
    best_topic, best_stats, best_gap = None, None, -1.0
    for ax, topic in PROFILE_AXES.items():
        score = (profile.get(ax) or {}).get("score")
        if score is None:
            continue
        stats = store_topic_stats(db, store.id, topic)
        if stats["n"] == 0:
            continue
        gap = abs(score - 50) + stats["n"] * 0.1  # 내 입맛이 극단인 축 × 데이터 많은 축 우선
        if gap > best_gap:
            best_topic, best_stats, best_gap = topic, stats, gap
    if best_topic is None:  # 프로필 축과 겹치는 데이터가 없으면 가게 최다 언급 토픽
        row = db.execute(
            select(AnonAspect.topic, func.count(AnonAspect.id).label("c"))
            .where(AnonAspect.store_id == store.id)
            .group_by(AnonAspect.topic).order_by(func.count(AnonAspect.id).desc())
        ).first()
        best_topic = row[0] if row else "맛_일반"
        best_stats = store_topic_stats(db, store.id, best_topic)

    # 표본 게이트 — n=0/n≤2는 LLM 미호출 + 통계 비공개 (꼬리표 없음)
    if best_stats["n"] == 0:
        return {"type": "first_guide", "title": "AI 첫주문 가이드",
                "message": "아직 이 가게의 속마음 데이터가 부족해요. 첫 기록의 주인공이 되어주세요!",
                "topic": best_topic, "stats": best_stats, "ai_mode": AI_MODE}
    if best_stats["n"] <= 2:
        return {"type": "first_guide", "title": "AI 첫주문 가이드",
                "message": f"이 가게의 속마음 데이터가 아직 충분히 쌓이지 않았어요. "
                           f"주문하시면 {nickname_call}의 기록이 큰 도움이 돼요!",
                "topic": best_topic, "stats": best_stats, "ai_mode": AI_MODE}

    # fit_hint — 서버가 (가게 경향 × 내 티어) 궁합 계산
    profile_axis = _profile_axis_payload(profile, best_topic)
    axis_key = AXIS_BY_TOPIC.get(best_topic)
    tier = (profile.get(axis_key) or {}).get("tier") if axis_key else None
    store_high = best_stats["kind"] == "axis" and best_stats["m_high"] * 2 >= best_stats["n"]
    fit_hint, action = None, None
    if store_high and tier is not None:
        if tier <= 1:
            fit_hint, action = "충돌", ACTION_SUGGESTIONS.get((best_topic, "over"))
        elif tier >= 3:
            fit_hint = "잘맞음"
    payload = {
        "nickname_call": nickname_call, "topic": best_topic,
        "stat_clause": _stat_clause(best_topic, best_stats),
        "profile_axis": profile_axis, "fit_hint": fit_hint, "action_slot": action,
    }
    note = "·참고용" if 3 <= best_stats["n"] <= 4 else ""
    if profile_axis is None and action is None:
        # 개인화 재료가 없으면 LLM 호출 없이 static 렌더 (반복 퇴화 방지 사전 게이트)
        body = static_synthesizer.first_guide(payload)
    else:
        body = _synthesize("P5", "first_guide", payload)
    message = body + render_footer(best_stats["n"], "관련 메모", note)
    return {"type": "first_guide", "title": "AI 첫주문 가이드", "message": message,
            "topic": best_topic, "stats": best_stats, "ai_mode": AI_MODE}


# ── 챗봇 (STEP 3-C · P3) ────────────────────────────────────────────────────
def build_chat_answer(db: Session, user: User, store: Store, question: str) -> dict:
    question = question[:200]  # 주입 표면·토큰 캡
    topic = classifier.classify(question)
    profile = profile_of(db, user.author_hash)

    pool_rows = db.scalars(select(TasteProfile).where(TasteProfile.author_hash != user.author_hash)).all()
    neighbors = find_neighbors(profile, {r.author_hash: r.data for r in pool_rows})
    neighbor_hashes = [h for h, _ in neighbors]

    stats, source = store_topic_stats(db, store.id, topic, author_hashes=neighbor_hashes), "neighbor"
    if stats["n"] < 3:  # 이웃 표본 부족 → 전체 풀 폴백 (출처 표기 변경)
        stats, source = store_topic_stats(db, store.id, topic), "all"

    base = {"question": question, "topic": topic, "source": source,
            "neighbor_count": len(neighbors), "stats": stats, "ai_mode": AI_MODE}

    # 표본 게이트 — n=0은 LLM 미호출 고정 응답, n≤2는 통계 비공개
    if stats["n"] == 0:
        return base | {"answer": "아직 이 주제의 속마음 데이터가 없어요. "
                                 "관련 기록이 쌓이면 바로 알려드릴게요!"}
    if stats["n"] <= 2:
        return base | {"answer": "이 주제의 속마음 데이터가 아직 충분히 쌓이지 않았어요. "
                                 "조금만 기다려주세요!"}

    payload = {
        "question": question, "store_name": store.name, "topic": topic,
        "stat_clause": _stat_clause(topic, stats),
        "own_memos": _own_memos_payload(db, user, store),
        "self_note": (_self_note_payload(db, user, store) or {}).get("quote"),
        "profile_axis": _profile_axis_payload(profile, topic),
        "nickname_call": f"{user.nickname}님",
        "action_slot": ACTION_SUGGESTIONS.get((topic, "over"))
        if stats["kind"] == "axis" and stats["m_high"] * 2 >= stats["n"]
        and (( _profile_axis_payload(profile, topic) or {}).get("score") or 50) < 40 else None,
    }
    source_label = "입맛이 비슷한 이용자 기록" if source == "neighbor" else "전체 이용자 기록"
    note = "·참고용" if 3 <= stats["n"] <= 4 else ""
    answer = _synthesize("P3", "chat_answer", payload) \
        + render_footer(stats["n"], source_label, note)
    return base | {"answer": answer}


CHAT_SUGGESTIONS = {
    "짠맛": "짜다는 리뷰 있어?", "매운맛": "많이 매워?", "느끼함": "느끼하진 않을까?",
    "양": "양은 넉넉한 편이야?", "위생": "위생은 괜찮아?", "배달속도": "배달 빨리 와?",
    "단맛": "많이 달아?", "식감": "식감은 어때?",
}


def chat_suggestions(db: Session, store: Store) -> list[str]:
    rows = db.execute(
        select(AnonAspect.topic, func.count(AnonAspect.id))
        .where(AnonAspect.store_id == store.id)
        .group_by(AnonAspect.topic).order_by(func.count(AnonAspect.id).desc()).limit(5)
    ).all()
    out = [CHAT_SUGGESTIONS[t] for t, _ in rows if t in CHAT_SUGGESTIONS][:2]
    return out + ["신메뉴 반응 어때?"]


# ── 사장님 대시보드 (개별 원문 없이 익명 집계만) ─────────────────────────────
def owner_dashboard(db: Session, store: Store) -> dict:
    cutoff = utcnow() - timedelta(days=30 * STATS_WINDOW_MONTHS)
    rows = db.scalars(select(AnonAspect).where(AnonAspect.store_id == store.id,
                                               AnonAspect.created_at >= cutoff)).all()
    by_topic: dict[str, dict] = {}
    for r in rows:
        e = by_topic.setdefault(r.topic, {"topic": r.topic, "count": 0, "negative": 0, "phrases": {}})
        e["count"] += 1
        if r.polarity == "negative":
            e["negative"] += 1
        if r.normalized:
            e["phrases"][r.normalized] = e["phrases"].get(r.normalized, 0) + 1
    items = []
    for e in sorted(by_topic.values(), key=lambda x: -x["count"]):
        top = sorted(e["phrases"].items(), key=lambda x: -x[1])[:3]
        items.append({"topic": e["topic"], "count": e["count"], "negative": e["negative"],
                      "top_phrases": [{"phrase": p, "count": c} for p, c in top]})
    return {"store_id": store.id, "store_name": store.name,
            "window_months": STATS_WINDOW_MONTHS, "total": len(rows), "topics": items}
