"""도메인 서비스 — 검증 관문 / 이중 저장 / 프로필 재계산 / 집계·게이트 / 가이드·챗 빌더.

숫자는 전부 여기(서버)서 센다. AI 계층(static/upstage)은 문장만 만든다.
"""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai import AI_MODE, classifier, synthesizer, tagger
from .config import STATS_WINDOW_MONTHS, utcnow
from .models import AnonAspect, Menu, MemoAspect, PersonalMemo, Store, TasteProfile, User
from .profile_engine import ALL_AXES, build_profile, find_neighbors
from .taxonomy import AXIS_LABELS, POLARITIES, PROFILE_AXES, TOPICS, is_valid_combo

AXIS_BY_TOPIC = {v: k for k, v in PROFILE_AXES.items()}  # "매운맛" -> "spicy"


# ── 검증 관문 (모델 무관 — static이든 LLM이든 같은 문을 통과) ────────────────
def validate_aspects(raw: dict, text: str) -> tuple[list[dict], str]:
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
        evidence = a.get("evidence")
        if evidence and evidence not in text:
            evidence = None  # evidence 부분문자열 검증 실패 → 폐기 (레코드는 유지)
        valid.append({
            "scope": scope, "topic": topic, "value": value,
            "polarity": a["polarity"],
            "intensity": max(1, min(3, int(a.get("intensity", 1)))),
            "evidence": evidence,
            "normalized": (a.get("normalized") or "")[:100],
            "source": a.get("source", "text"),
        })
    reorder = raw.get("reorder_intent", "none")
    if reorder not in {"pos", "neg", "conditional", "none"}:
        reorder = "none"
    return valid, reorder


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
    aspects, reorder = validate_aspects(tagger.tag(text, emotion, chips), text)

    memo = PersonalMemo(user_id=user.id, store_id=store.id, menu_ids=menu_ids,
                        emotion=emotion, text=text, chips=chips,
                        reorder_intent=reorder, created_at=utcnow())
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
        # 값 축 프레이밍("생각보다 맵다" 취지 건수) — 가이드용
        "m_high": sum(1 for r in rows if axis and r.value == high),
        # 만족 프레이밍(긍정 극성 건수) — 챗봇용
        "m_pos": sum(1 for r in rows if r.polarity == "positive"),
        "kind": "axis" if axis else "polarity", "high_label": high,
        "months": STATS_WINDOW_MONTHS,
    }


def _personal_line(topic: str, profile: dict, nickname: str) -> str:
    ax = AXIS_BY_TOPIC.get(topic)
    tier = (profile.get(ax) or {}).get("tier") if ax else None
    if topic == "매운맛":
        if tier is not None and tier <= 1:
            return f"매운 걸 잘 못 드시는 {nickname}님에게는 순한맛(1단계) 옵션이 안전할 것 같아요."
        if tier is not None and tier >= 3:
            return f"매운맛을 즐기시는 {nickname}님 입맛에 잘 맞을 것 같아요!"
    if topic == "느끼함":
        if tier is not None and tier >= 3:
            return f"평소 느끼한 음식을 즐겨 드시는 {nickname}님에게는 좋은 선택이 될 것 같아요!"
        if tier is not None and tier <= 1:
            return f"느끼함에 민감하신 {nickname}님은 담백한 메뉴가 안전할 것 같아요."
    if topic == "짠맛" and tier is not None and tier <= 1:
        return f"간이 센 음식을 잘 못 드시는 {nickname}님은 참고해서 골라보세요."
    if topic == "단맛" and tier is not None and tier >= 3:
        return f"달달한 맛을 즐기시는 {nickname}님에게 잘 맞을 수 있어요!"
    return f"{nickname}님 주문 전에 참고해보세요!"


def _stat_line(topic: str, stats: dict) -> str:
    if stats["kind"] == "axis":
        return f"{topic} 관련 메모 {stats['n']}건 중 {stats['m_high']}건이 '생각보다 {stats['high_label']}'는 취지였어요."
    return f"{topic} 관련 메모 {stats['n']}건 중 {stats['m_pos']}건이 만족했어요."


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

    if my_memos:  # 재주문 가게 → 넛지
        aspects = db.execute(
            select(MemoAspect, PersonalMemo)
            .join(PersonalMemo, MemoAspect.memo_id == PersonalMemo.id)
            .where(PersonalMemo.user_id == user.id, PersonalMemo.store_id == store.id)
            .order_by(PersonalMemo.created_at.desc())
        ).all()
        satisfied, latest_neg = [], None
        for asp, memo in aspects:
            if asp.polarity == "positive" and asp.topic not in satisfied:
                satisfied.append(asp.topic)
            if latest_neg is None and asp.polarity == "negative":
                axis = TOPICS[asp.topic]["axis"]
                if axis and asp.value == axis[2]:
                    direction = "over"
                elif axis and asp.value == axis[0]:
                    direction = "under"
                else:
                    direction = "neg"
                menu_names = [m.name for m in db.scalars(select(Menu).where(Menu.id.in_(memo.menu_ids or []))).all()]
                latest_neg = {"menu_name": menu_names[0] if menu_names else None,
                              "topic": asp.topic, "normalized": asp.normalized or "아쉬움",
                              "direction": direction}
        cond_note = next((m.text for m in my_memos if m.reorder_intent == "conditional"), None)
        message = synthesizer.nudge({
            "nickname": user.nickname, "latest_negative": latest_neg,
            "satisfied_topics": satisfied, "conditional_note": cond_note,
        })
        return {"type": "renudge", "title": "AI 재주문 가이드", "message": message,
                "evidence_memos": [_memo_badge(db, m) for m in my_memos[:5]]}

    # 첫 주문 가게 → 집단 속마음 × 내 프로필 대조
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

    m_for_topic = best_stats["m_high"] if best_stats["kind"] == "axis" else best_stats["m_pos"]
    message = synthesizer.first_guide({
        "nickname": user.nickname, "topic": best_topic,
        "n": best_stats["n"], "m": m_for_topic, "months": best_stats["months"],
        "sample_label": "참고용" if 3 <= best_stats["n"] <= 4 else None,
        "stat_line": _stat_line(best_topic, best_stats),
        "personal_line": _personal_line(best_topic, profile, user.nickname),
    })
    return {"type": "first_guide", "title": "AI 첫주문 가이드", "message": message,
            "topic": best_topic, "stats": best_stats}


# ── 챗봇 (STEP 3-C) ─────────────────────────────────────────────────────────
def build_chat_answer(db: Session, user: User, store: Store, question: str) -> dict:
    topic = classifier.classify(question)
    profile = profile_of(db, user.author_hash)

    pool_rows = db.scalars(select(TasteProfile).where(TasteProfile.author_hash != user.author_hash)).all()
    neighbors = find_neighbors(profile, {r.author_hash: r.data for r in pool_rows})
    neighbor_hashes = [h for h, _ in neighbors]

    stats, source = store_topic_stats(db, store.id, topic, author_hashes=neighbor_hashes), "neighbor"
    if stats["n"] < 3:  # 이웃 표본 부족 → 전체 풀 폴백 (출처 표기 변경)
        stats, source = store_topic_stats(db, store.id, topic), "all"

    # LLM 합성용 재료: 질문 topic에 해당하는 내 프로필 축 (활성 축만)
    axis_key = AXIS_BY_TOPIC.get(topic) or ("texture" if topic == "식감" else None)
    profile_axis = None
    if axis_key:
        e = profile.get(axis_key) or {}
        if e.get("score") is not None:
            profile_axis = {"label": AXIS_LABELS[axis_key], "score": e["score"],
                            "tier_name": e.get("tier_name")}

    # 챗봇은 만족 프레이밍("N건 중 M건이 만족했어요")으로 통일
    answer = synthesizer.chat_answer({
        "topic": topic, "n": stats["n"], "m": stats["m_pos"], "months": stats["months"],
        "source": source, "m_desc": "만족했어요",
        "personal_line": _personal_line(topic, profile, user.nickname),
        # 이하 LLM 전용 재료 (static은 무시)
        "question": question, "store_name": store.name,
        "nickname": user.nickname, "profile_axis": profile_axis,
    })
    return {"question": question, "topic": topic, "answer": answer,
            "source": source, "neighbor_count": len(neighbors), "stats": stats,
            "ai_mode": AI_MODE}


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
