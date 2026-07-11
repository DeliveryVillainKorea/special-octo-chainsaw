"""시드 — 시연 영상(속마음작성·가게찾기·가이드및챗봇.mp4) 재현 데이터.

- 가게 6곳 / 고객 20 (기요 + 페르소나 6 + 일반 13) / 사장님 5
- 기요의 배지·넛지 메모는 영상 원문 그대로
- 데모 수치 고정: 매콤달콤 매운맛 12건 중 '맵다' 7건 / 레파레피자 느끼함 15건 중 만족 9건
- 익명 배경 풀 30명(author_hash만 존재, 원문 없음)

실행:  python -m seed.run_seed   (backend/ 에서)
"""
import uuid
from datetime import datetime, timedelta

from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.models import AnonAspect, Menu, PersonalMemo, Store, TasteProfile, User
from app.services import recompute_hash_profile, recompute_user_profile, validate_aspects
from app.ai.static_ai import StaticTagger, extract_reorder, extract_self_note

tagger = StaticTagger()

STORES = [
    ("엄마손맛닭볶음탕-영통점", "한식", ["레전드 닭볶음탕(비조리)", "순한맛 닭볶음탕"]),
    ("굽네치킨&피자-병점2동점", "치킨", ["고추바사삭", "볼케이노치킨", "허니멜로"]),
    ("까르보네-동탄센트럴파크점", "양식", ["크림치킨", "까르보나라", "크림파스타", "오일파스타"]),
    ("롯데리아-화성센트럴파크점", "버거", ["한우불고기 버거세트", "치즈스틱", "초코쉐이크"]),
    ("레파레피자-망포역점", "피자", ["고구마 피자(R)", "하와이안 피자", "페퍼로니 피자", "버터갈릭 쉬림프 피자"]),
    ("매콤달콤 로제떡볶이-영통점", "분식", ["로제떡볶이", "오리지널 떡볶이", "치즈 추가"]),
]

D = lambda m, d: datetime(2026, m, d, 12, 0)


def seed_memo(db, user, store, menus_by_store, menu_names, emotion, text, chips,
              created, aspects=None):
    """이중 저장 시드 헬퍼 — aspects 명시 시 태거 우회(데모 정합), 아니면 static 태거."""
    if aspects is None:
        raw = tagger.tag(text, emotion, chips)
    else:
        raw = {"aspects": aspects, "reorder_intent": extract_reorder(text),
               "self_note": extract_self_note(text)}
    valid, reorder, self_note = validate_aspects(raw, text)
    menu_ids = [menus_by_store[store.id][n].id for n in menu_names]
    memo = PersonalMemo(user_id=user.id, store_id=store.id, menu_ids=menu_ids,
                        emotion=emotion, text=text, chips=chips,
                        reorder_intent=reorder, self_note=self_note, created_at=created)
    db.add(memo)
    db.flush()
    for a in valid:
        from app.models import MemoAspect
        db.add(MemoAspect(memo_id=memo.id, **a))
        db.add(AnonAspect(author_hash=user.author_hash, store_id=store.id,
                          menu_id=menu_ids[0] if menu_ids else None, memo_group=f"m{memo.id}",
                          scope=a["scope"], topic=a["topic"], value=a["value"],
                          polarity=a["polarity"], intensity=a["intensity"],
                          normalized=a["normalized"], created_at=created))
    return memo


def A(topic, value, pol, inten, norm, evidence=None):
    return {"scope": "menu", "topic": topic, "value": value, "polarity": pol,
            "intensity": inten, "evidence": evidence, "normalized": norm, "source": "text"}


def main():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()

    # ── 가게 / 메뉴 / 계정 ──────────────────────────────────────────────
    owners = [User(nickname=f"사장님{i+1}", password_hash=hash_password("demo1234"),
                   role="owner", author_hash=uuid.uuid4().hex) for i in range(5)]
    db.add_all(owners)
    db.flush()

    stores, menus_by_store = [], {}
    for i, (name, cat, menu_names) in enumerate(STORES):
        s = Store(name=name, category=cat, owner_id=owners[i].id if i < 5 else None)
        db.add(s)
        db.flush()
        stores.append(s)
        menus_by_store[s.id] = {}
        for mn in menu_names:
            m = Menu(store_id=s.id, name=mn)
            db.add(m)
            db.flush()
            menus_by_store[s.id][mn] = m
    st_mom, st_goobne, st_carbo, st_ria, st_pizza, st_tteok = stores

    def make_user(nick):
        u = User(nickname=nick, password_hash=hash_password("demo1234"),
                 role="customer", author_hash=uuid.uuid4().hex)
        db.add(u)
        return u

    giyo = make_user("기요")
    p1, p2, p3 = make_user("맵찔이대학생"), make_user("헬스인"), make_user("야근족")
    p4, p5, p6 = make_user("자취5년차"), make_user("아기엄마"), make_user("매운맛마니아")
    generics = [make_user(f"이용자{i:02d}") for i in range(8, 21)]  # 13명
    db.flush()
    customers = [giyo, p1, p2, p3, p4, p5, p6] + generics

    # ── 기요 메모 (시연 영상 원문) ──────────────────────────────────────
    SM = lambda *args, **kw: seed_memo(db, giyo, *args, **kw)
    # 프로필 재료: 느끼함 3건(활성·선호↑) / 단맛 2건 / 매운맛 2건 (라이브 작성으로 단맛 3건째 활성화 연출)
    SM(st_carbo, menus_by_store, ["크림파스타"], "like",
       "크림파스타 꾸덕꾸덕 진하게 느끼한 게 최고임", [], D(5, 10),
       aspects=[A("느끼함", "느끼", "positive", 2, "꾸덕해요", "크림파스타 꾸덕꾸덕 진하게 느끼한 게 최고임")])
    SM(st_ria, menus_by_store, ["치즈스틱"], "like",
       "치즈스틱 기름진 맛이 굿", [], D(5, 18),
       aspects=[A("느끼함", "느끼", "positive", 1, "기름져요", "치즈스틱 기름진 맛이 굿")])
    SM(st_carbo, menus_by_store, ["크림치킨"], "like",
       "버터 풍미 느끼하니 좋다", [], D(5, 25),
       aspects=[A("느끼함", "느끼", "positive", 1, "느끼해요", "버터 풍미 느끼하니 좋다")])
    SM(st_ria, menus_by_store, ["초코쉐이크"], "like",
       "초코쉐이크 달달함 최고", [], D(6, 1),
       aspects=[A("단맛", "달다", "positive", 1, "달달해요", "초코쉐이크 달달함 최고")])
    SM(st_goobne, menus_by_store, ["허니멜로"], "like",
       "허니 소스 달아서 좋음", [], D(6, 5),
       aspects=[A("단맛", "달다", "positive", 1, "달아요", "허니 소스 달아서 좋음")])
    SM(st_goobne, menus_by_store, ["볼케이노치킨"], "like",
       "볼케이노 맵찔이는 못 먹을 맛인데 난 좋다", [], D(6, 3),
       aspects=[A("매운맛", "맵다", "positive", 2, "매워요", "볼케이노 맵찔이는 못 먹을 맛인데 난 좋다")])
    SM(st_pizza, menus_by_store, ["페퍼로니 피자"], "like",
       "페퍼로니 매콤해서 좋음 토핑도 가득", [], D(6, 8),
       aspects=[A("매운맛", "맵다", "positive", 1, "매콤해요", "페퍼로니 매콤해서 좋음 토핑도 가득")])
    # 가게 리스트 배지 3건 (영상 그대로, 2026-06-12)
    SM(st_goobne, menus_by_store, ["고추바사삭", "볼케이노치킨"], "like",
       "고바삭이 맛도리임, 담에 시킬 땐 꼭 소스 추가ㄱㄱ", ["좋아하는 맛이에요"], D(6, 12))
    SM(st_carbo, menus_by_store, ["크림치킨", "까르보나라"], "dislike",
       "까르보나라 별로, 근데 오일은 맛있었음. 미래의 나야... 담에는 까르보나라 절대 ㄴㄴ",
       ["내 취향이 아니에요"], D(6, 12))
    SM(st_ria, menus_by_store, ["한우불고기 버거세트"], "like",
       "감튀가 촉촉 눅눅해서 좋았음. 역시 롯리는 눅눅감튀지!!! 근데 담에는 콜라 큰 사이즈 시켜야겠음 모자라ㅠㅠ..",
       [], D(6, 12))
    # 레파레피자 넛지 근거 (영상: 고구마 피자 양파 아쉬움)
    SM(st_pizza, menus_by_store, ["하와이안 피자"], "like",
       "하와이안은 늘 만족! 파인애플 존맛", [], D(6, 10))
    SM(st_pizza, menus_by_store, ["고구마 피자(R)"], "dislike",
       "아놔,, 진짜 맛은 있는데... 양파가 넘 조금 들었음. 고구마 피자는 양파맛으로 먹는 건데,,", [], D(6, 12),
       aspects=[A("기타", None, "negative", 2, "양파 토핑이 아쉬워요", "양파가 넘 조금 들었음")])

    # ── 페르소나 메모 (프로필 축 재료 — 가게 1~4만 사용, 예약 통계 오염 방지) ──
    def persona_memos(user, spec):
        """spec: [(store, menu, emotion, topic, value, pol, inten, text)]"""
        for i, (store, menu, emotion, topic, value, pol, inten, text) in enumerate(spec):
            # 최근 날짜로 두어 반감기 가중치 ≈1 유지 (티어 경계가 프라이어 쪽으로 밀리지 않게)
            norm = f"{value or topic} " + ("좋아요" if pol == "positive" else "아쉬워요")
            seed_memo(db, user, store, menus_by_store, [menu], emotion, text, [],
                      D(7, 3) + timedelta(days=i % 6, hours=i),
                      aspects=[A(topic, value, pol, inten, norm, text)])

    persona_memos(p1, [  # 맵찔이: spicy 39(t1)↓ · sweet 65(t3)↑ · greasy 39(t1)↓
        (st_goobne, "고추바사삭", "like", "매운맛", "순함", "positive", 1, "순한 맛이라 좋다"),
        (st_mom, "순한맛 닭볶음탕", "like", "매운맛", "순함", "positive", 1, "순한 양념이라 애용"),
        (st_goobne, "볼케이노치킨", "dislike", "매운맛", "맵다", "negative", 3, "레전드로 맵다 혀 나감"),
        (st_ria, "초코쉐이크", "like", "단맛", "달다", "positive", 1, "달달해서 힐링"),
        (st_ria, "한우불고기 버거세트", "like", "단맛", "달다", "positive", 1, "소스 달아서 좋음"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "단맛", "달다", "positive", 1, "양념 달달해서 좋다"),
        (st_carbo, "크림파스타", "like", "느끼함", "담백", "positive", 1, "담백하게 잘 먹음"),
        (st_carbo, "오일파스타", "like", "느끼함", "담백", "positive", 1, "담백해서 부담 없음"),
        (st_carbo, "까르보나라", "dislike", "느끼함", "느끼", "negative", 3, "너무 느끼해서 반 남김"),
    ])
    persona_memos(p2, [  # 헬스인: salty 39↓ · sweet 39↓ · greasy 39↓
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "짠맛", "싱겁다", "positive", 1, "삼삼해서 좋다"),
        (st_ria, "한우불고기 버거세트", "like", "짠맛", "싱겁다", "positive", 1, "간이 슴슴해서 굿"),
        (st_mom, "레전드 닭볶음탕(비조리)", "dislike", "짠맛", "짜다", "negative", 3, "국물이 개짜다"),
        (st_mom, "순한맛 닭볶음탕", "like", "단맛", "안달다", "positive", 1, "안 달아서 좋음"),
        (st_goobne, "허니멜로", "dislike", "단맛", "달다", "negative", 3, "당 폭탄"),
        (st_ria, "초코쉐이크", "like", "단맛", "안달다", "positive", 1, "당 조절 성공"),
        (st_carbo, "오일파스타", "like", "느끼함", "담백", "positive", 1, "담백 그 자체"),
        (st_goobne, "고추바사삭", "like", "느끼함", "담백", "positive", 1, "기름지지 않아 좋다"),
        (st_carbo, "크림파스타", "dislike", "느끼함", "느끼", "negative", 3, "크림 너무 헤비함"),
    ])
    persona_memos(p3, [  # 야근족: spicy 65↑ · salty 65↑ · greasy 65↑
        (st_goobne, "볼케이노치킨", "like", "매운맛", "맵다", "positive", 1, "화끈해서 스트레스 풀림"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "매운맛", "맵다", "positive", 1, "칼칼해서 좋다"),
        (st_goobne, "볼케이노치킨", "like", "매운맛", "맵다", "positive", 1, "역시 매운맛"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "짠맛", "짜다", "positive", 1, "간이 세야 밥이 감"),
        (st_ria, "한우불고기 버거세트", "like", "짠맛", "짜다", "positive", 1, "짭짤한 게 좋다"),
        (st_goobne, "고추바사삭", "like", "짠맛", "짜다", "positive", 1, "짭조름 최고"),
        (st_carbo, "크림파스타", "like", "느끼함", "느끼", "positive", 1, "꾸덕할수록 좋다"),
        (st_carbo, "크림치킨", "like", "느끼함", "느끼", "positive", 1, "느끼해도 잘 먹음"),
        (st_ria, "치즈스틱", "like", "느끼함", "느끼", "positive", 1, "치즈 풍미 좋다"),
    ])
    persona_memos(p4, [  # 자취5년차: 3축 모두 50(t2)
        (st_goobne, "고추바사삭", "like", "매운맛", "보통", "positive", 1, "적당히 맵다"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "매운맛", "보통", "positive", 1, "무난한 맵기"),
        (st_goobne, "볼케이노치킨", "like", "매운맛", "보통", "positive", 1, "먹을만한 맵기"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "짠맛", "보통", "positive", 1, "간이 딱"),
        (st_ria, "한우불고기 버거세트", "like", "짠맛", "보통", "positive", 1, "간 적당"),
        (st_carbo, "까르보나라", "like", "짠맛", "보통", "positive", 1, "간 무난"),
        (st_ria, "초코쉐이크", "like", "단맛", "보통", "positive", 1, "적당히 달다"),
        (st_goobne, "허니멜로", "like", "단맛", "보통", "positive", 1, "단맛 무난"),
        (st_mom, "순한맛 닭볶음탕", "like", "단맛", "보통", "positive", 1, "달지 않고 적당"),
    ])
    persona_memos(p5, [  # 아기엄마: spicy 35(t1)↓ · sweet 50 · salty 40
        (st_mom, "순한맛 닭볶음탕", "like", "매운맛", "순함", "positive", 1, "아이랑 먹기 좋은 순한맛"),
        (st_goobne, "고추바사삭", "like", "매운맛", "순함", "positive", 1, "순해서 아이도 잘 먹음"),
        (st_ria, "한우불고기 버거세트", "like", "매운맛", "순함", "positive", 1, "안 매워서 가족용"),
        (st_ria, "초코쉐이크", "like", "단맛", "보통", "positive", 1, "적당한 단맛"),
        (st_mom, "순한맛 닭볶음탕", "like", "단맛", "보통", "positive", 1, "단맛 적당"),
        (st_goobne, "허니멜로", "like", "단맛", "보통", "positive", 1, "무난한 단맛"),
        (st_mom, "순한맛 닭볶음탕", "like", "짠맛", "싱겁다", "positive", 1, "슴슴해서 좋다"),
        (st_ria, "치즈스틱", "like", "짠맛", "싱겁다", "positive", 1, "안 짜서 좋음"),
        (st_carbo, "오일파스타", "like", "짠맛", "보통", "positive", 1, "간 무난"),
    ])
    persona_memos(p6, [  # 매운맛마니아: spicy 67(t3)↑ · greasy 65↑ · salty 65↑
        (st_goobne, "볼케이노치킨", "like", "매운맛", "맵다", "positive", 1, "이 맛에 먹지"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "매운맛", "맵다", "positive", 1, "칼칼함 최고"),
        (st_goobne, "볼케이노치킨", "like", "매운맛", "맵다", "positive", 1, "더 매워도 됨"),
        (st_goobne, "고추바사삭", "like", "매운맛", "맵다", "positive", 1, "맵부심 만족"),
        (st_carbo, "크림파스타", "like", "느끼함", "느끼", "positive", 1, "꾸덕 좋아"),
        (st_ria, "치즈스틱", "like", "느끼함", "느끼", "positive", 1, "느끼함 즐김"),
        (st_carbo, "크림치킨", "like", "느끼함", "느끼", "positive", 1, "부드럽고 진함"),
        (st_mom, "레전드 닭볶음탕(비조리)", "like", "짠맛", "짜다", "positive", 1, "짭짤 최고"),
        (st_goobne, "고추바사삭", "like", "짠맛", "짜다", "positive", 1, "간 센 게 좋다"),
        (st_ria, "한우불고기 버거세트", "like", "짠맛", "짜다", "positive", 1, "짭짤해서 좋음"),
    ])

    # ── 익명 배경 풀 30명 (원문 없음 — author_hash + 태깅 레코드만) ──────
    bg_hashes = [f"bg{i:03d}" + uuid.uuid4().hex[:8] for i in range(30)]
    ARCHETYPES = [
        {"매운맛": [("순함", "positive", 1), ("순함", "positive", 1), ("맵다", "negative", 3)],
         "단맛": [("달다", "positive", 1)] * 3,
         "느끼함": [("담백", "positive", 1), ("담백", "positive", 1), ("느끼", "negative", 3)]},
        {"매운맛": [("맵다", "positive", 1)] * 3,
         "짠맛": [("짜다", "positive", 1)] * 3,
         "느끼함": [("느끼", "positive", 1)] * 3},
        {"느끼함": [("담백", "positive", 1)] * 3,
         "짠맛": [("싱겁다", "positive", 1), ("싱겁다", "positive", 1), ("짜다", "negative", 3)],
         "단맛": [("안달다", "positive", 1)] * 3},
        {"매운맛": [("보통", "positive", 1)] * 3,
         "짠맛": [("보통", "positive", 1)] * 3,
         "단맛": [("보통", "positive", 1)] * 3},
    ]
    RESERVED = {(st_tteok.id, "매운맛"), (st_pizza.id, "느끼함")}  # 데모 수치 고정용

    def add_anon(author_hash, store, topic, value, pol, inten, norm, created):
        db.add(AnonAspect(author_hash=author_hash, store_id=store.id, menu_id=None,
                          memo_group=uuid.uuid4().hex, scope="menu", topic=topic,
                          value=value, polarity=pol, intensity=inten,
                          normalized=norm, created_at=created))

    day = 0
    for i, h in enumerate(bg_hashes):
        arche = ARCHETYPES[i % len(ARCHETYPES)]
        for topic, recs in arche.items():
            for value, pol, inten in recs:
                store = stores[(i + day) % 4]  # 가게 1~4만 (예약 조합 회피)
                norm = f"{value} " + ("좋아요" if pol == "positive" else "아쉬워요")
                add_anon(h, store, topic, value, pol, inten, norm,
                         D(3, 1) + timedelta(days=(i * 7 + day) % 120))
                day += 1

    # 데모 수치 고정 ①: 매콤달콤 매운맛 12건 — '맵다' 7건 (가이드: "12건 중 7건")
    spicy_recs = ([("맵다", "negative", 2, "생각보다 매워요")] * 4
                  + [("맵다", "positive", 1, "맵찔이 주의")] * 3
                  + [("보통", "positive", 1, "무난한 맵기")] * 3
                  + [("순함", "positive", 1, "순한 편이에요"), ("순함", "negative", 1, "싱겁게 순해요")])
    for j, (v, p, it, nm) in enumerate(spicy_recs):
        add_anon(bg_hashes[j % 30], st_tteok, "매운맛", v, p, it, nm, D(4, 2) + timedelta(days=j * 6))

    # 데모 수치 고정 ②: 레파레피자 느끼함 15건 — 만족 9건 (챗봇: "15건 중 9건 만족")
    greasy_recs = ([("느끼", "positive", 1, "고소하고 진해요")] * 6
                   + [("담백", "positive", 1, "생각보다 담백해요")] * 3
                   + [("느끼", "negative", 2, "좀 느끼해요")] * 6)
    for j, (v, p, it, nm) in enumerate(greasy_recs):
        add_anon(bg_hashes[j % 30], st_pizza, "느끼함", v, p, it, nm, D(4, 5) + timedelta(days=j * 5))

    # 사장님 대시보드 재료: 엄마손맛 단맛/짠맛/가격 피드백
    mom_recs = ([("단맛", "달다", "negative", 2, "너무 달아요")] * 6
                + [("단맛", "달다", "positive", 1, "달달해서 좋아요")] * 2
                + [("짠맛", "짜다", "negative", 2, "너무 짜요")] * 4
                + [("짠맛", "보통", "positive", 1, "간이 딱 좋아요")]
                + [("가격", None, "negative", 1, "비싼 것 같아요")] * 2
                + [("가격", None, "positive", 1, "가성비가 좋아요")])
    for j, (t, v, p, it, nm) in enumerate(mom_recs):
        add_anon(bg_hashes[(j + 11) % 30], st_mom, t, v, p, it, nm, D(4, 8) + timedelta(days=j * 5))

    # 부가 재료: 굽네 식감 / 레파레 양
    for j in range(4):
        add_anon(bg_hashes[(j + 3) % 30], st_goobne, "식감", None, "positive", 1, "바삭", D(5, 2 + j * 7))
    add_anon(bg_hashes[9], st_goobne, "식감", None, "negative", 1, "눅눅", D(5, 30))
    for j in range(3):
        add_anon(bg_hashes[(j + 17) % 30], st_pizza, "양", "많다", "positive", 1, "양이 많아요", D(5, 3 + j * 8))
    add_anon(bg_hashes[21], st_pizza, "양", "적다", "negative", 2, "양이 적어요", D(6, 2))

    db.flush()

    # ── 프로필 일괄 계산 (로그인 유저 + 익명 풀 동일 함수) ────────────────
    for u in customers:
        recompute_user_profile(db, u)
    for h in bg_hashes:
        recompute_hash_profile(db, h)
    db.commit()

    n_memo = db.query(PersonalMemo).count()
    n_anon = db.query(AnonAspect).count()
    n_prof = db.query(TasteProfile).count()
    print(f"시드 완료 — 가게 {len(stores)} / 고객 {len(customers)} / 사장님 {len(owners)}")
    print(f"개인 메모 {n_memo}건 / 익명 풀 레코드 {n_anon}건 / 입맛 프로필 {n_prof}명")
    print("\n데모 계정 (비밀번호 전부 demo1234)")
    print("  고객: 기요(데모 주인공) · 맵찔이대학생 · 헬스인 · 야근족 · 자취5년차 · 아기엄마 · 매운맛마니아 · 이용자08~20")
    print("  사장님: 사장님1(엄마손맛닭볶음탕) ~ 사장님5(레파레피자)")
    db.close()


if __name__ == "__main__":
    main()
