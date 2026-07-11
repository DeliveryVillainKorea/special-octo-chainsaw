"""입맛 프로필 산정 엔진 — 입맛프로필_산정체계.html §3/§5/§7 구현.

전부 순수 함수 (LLM 0회, DB 0회). 단위 테스트: tests/test_profile_engine.py
"""
from datetime import datetime
from math import floor

from .config import utcnow
from .taxonomy import HIGH_WORDS, LOW_WORDS, PROFILE_AXES, S_MAP, TIER_NAMES

PRIOR_SCORE = 50.0        # 사전값
PRIOR_WEIGHT = 2.0        # 가상 표본 2건
HALF_LIFE_DAYS = 90.0     # 최근성 반감기
MIN_N_ACTIVE = 3          # 축별 3건 미만이면 "수집 중" (점수 비노출)


def _recency_weight(created_at: datetime, now: datetime) -> float:
    days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (days / HALF_LIFE_DAYS)


def _boundary_direction(topic: str, normalized: str) -> int | None:
    """부정 & S=50 경계: normalized 방향 어휘로 +1(과함)/-1(부족), 판정 불가 None(제외)."""
    text = normalized or ""
    for w in HIGH_WORDS.get(topic, []):
        if w in text:
            return +1
    for w in LOW_WORDS.get(topic, []):
        if w in text:
            return -1
    return None


def target_of(topic: str, value: str | None, polarity: str, intensity: int,
              normalized: str = "") -> float | None:
    """메모 1건의 aspect → target(적정 자극 추정치). 계산 제외 시 None."""
    s_map = S_MAP[topic]
    S = float(s_map.get(value, 50)) if value is not None else 50.0  # TODO: 메뉴 최빈값 폴백
    if polarity == "positive":
        return S
    if polarity == "negative":
        if S > 50:
            return S - 10 * intensity   # 과함 → 내려잡음
        if S < 50:
            return S + 10 * intensity   # 부족 → 올려잡음
        d = _boundary_direction(topic, normalized)
        if d is None:
            return None                 # 틀린 확신보다 빈칸 (원칙 ② 침묵)
        return S - 10 * intensity if d > 0 else S + 10 * intensity
    return None  # 중립 → 침묵


def tier_of(score: float) -> int:
    return min(4, floor(max(0.0, min(100.0, score)) / 20))


def axis_score(topic: str, aspects: list[dict], now: datetime | None = None) -> dict:
    """4축 공통 공식. aspects: {value, polarity, intensity, normalized, created_at, ref_id}."""
    now = now or utcnow()
    contribs: list[tuple[float, float, object]] = []
    for a in aspects:
        t = target_of(topic, a.get("value"), a["polarity"], a.get("intensity", 1),
                      a.get("normalized", ""))
        if t is None:
            continue
        contribs.append((_recency_weight(a["created_at"], now), t, a.get("ref_id")))
    n = len(contribs)
    if n == 0:
        return {"score": None, "tier": None, "n": 0, "evidence_ids": []}
    w_sum = sum(w for w, _, _ in contribs)
    raw = (PRIOR_SCORE * PRIOR_WEIGHT + sum(w * t for w, t, _ in contribs)) / (PRIOR_WEIGHT + w_sum)
    evidence = [r for _, _, r in contribs if r is not None]
    if n < MIN_N_ACTIVE:
        return {"score": None, "tier": None, "n": n, "evidence_ids": evidence}  # "수집 중"
    score = round(raw)
    return {"score": score, "tier": tier_of(score), "n": n, "evidence_ids": evidence}


def texture_score(texture_aspects: list[dict], total_memos: int) -> dict:
    """식감 = 민감도. 언급 빈도 60% + 언급 강도 40%. 저빈도 축 보정 ×3."""
    n = len(texture_aspects)
    if n == 0 or total_memos == 0:
        return {"score": None, "tier": None, "n": 0, "likes": [], "dislikes": [], "evidence_ids": []}
    mention_ratio = n / total_memos
    avg_intensity = sum(a.get("intensity", 1) for a in texture_aspects) / n
    raw = 100 * (0.6 * min(mention_ratio * 3, 1.0) + 0.4 * avg_intensity / 3)

    def top_normalized(polarity: str) -> list[str]:
        counts: dict[str, int] = {}
        for a in texture_aspects:
            if a["polarity"] == polarity and a.get("normalized"):
                counts[a["normalized"]] = counts.get(a["normalized"], 0) + 1
        return [k for k, _ in sorted(counts.items(), key=lambda x: -x[1])[:2]]

    evidence = [a.get("ref_id") for a in texture_aspects if a.get("ref_id") is not None]
    out = {"n": n, "likes": top_normalized("positive"), "dislikes": top_normalized("negative"),
           "evidence_ids": evidence}
    if n < MIN_N_ACTIVE:
        out.update({"score": None, "tier": None})
    else:
        score = round(raw)
        out.update({"score": score, "tier": tier_of(score)})
    return out


def build_profile(aspects_by_topic: dict[str, list[dict]], total_memos: int,
                  now: datetime | None = None) -> dict:
    """태깅 레코드 → 5축 프로필. aspects_by_topic 키는 한국어 토픽명."""
    now = now or utcnow()
    profile: dict = {}
    for key, topic in PROFILE_AXES.items():
        entry = axis_score(topic, aspects_by_topic.get(topic, []), now)
        entry["tier_name"] = TIER_NAMES[key][entry["tier"]] if entry["tier"] is not None else None
        profile[key] = entry
    tex = texture_score(aspects_by_topic.get("식감", []), total_memos)
    tex["tier_name"] = TIER_NAMES["texture"][tex["tier"]] if tex["tier"] is not None else None
    profile["texture"] = tex
    profile["updated_at"] = now.isoformat()
    return profile


# ── 입맛 이웃 (§7) ──────────────────────────────────────────────────────────
ALL_AXES = ["spicy", "salty", "sweet", "greasy", "texture"]
SIM_THRESHOLD = 0.8
MIN_SHARED_AXES = 3
NEIGHBOR_K = 20


def similarity(p: dict, q: dict) -> float | None:
    """활성 축(n>=3, score 있음)이 3개 이상 겹칠 때만 정의. 1 − Σ|Δ|/(100×축수)."""
    shared = [ax for ax in ALL_AXES
              if p.get(ax, {}).get("score") is not None and q.get(ax, {}).get("score") is not None]
    if len(shared) < MIN_SHARED_AXES:
        return None
    dist = sum(abs(p[ax]["score"] - q[ax]["score"]) for ax in shared)
    return 1 - dist / (100.0 * len(shared))


def find_neighbors(me: dict, pool: dict[str, dict], k: int = NEIGHBOR_K) -> list[tuple[str, float]]:
    """pool: author_hash -> profile. 유사도 ≥0.8 상위 k명."""
    scored = []
    for h, prof in pool.items():
        sim = similarity(me, prof)
        if sim is not None and sim >= SIM_THRESHOLD:
            scored.append((h, round(sim, 4)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]
