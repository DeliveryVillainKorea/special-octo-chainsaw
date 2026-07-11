# 속마음 리뷰 백엔드 (요기요 해커톤 PoC)

비공개 속마음 메모 → 태깅(17종 닫힌 어휘) → 입맛 프로필(5축) → 개인화 가이드/챗봇.

- **AI는 현재 static 모드** (`LLM_PROVIDER=static`): 태깅 = 태그 칩 결정적 매핑 + 키워드 규칙(시드 사전), 답변 = f-string 템플릿. 튜닝 후 `app/ai/upstage_ai.py`(Upstage solar-pro3 + json_schema)만 구현해 env로 전환.
- **점수·표본 수는 전부 서버가 계산** (LLM 0회 원칙) — 프로필 엔진은 순수 함수라 단위 테스트로 검증됨 (`tests/`).
- **이중 저장**: `personal_memos`(원문, 본인만) + `anon_aspects`(원문 없음, author_hash) — 삭제 시 프로필까지 전파 재계산.

## 실행 (Python 3.10+)

```bash
cd backend
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m seed.run_seed        # DB 초기화 + 시연 데이터
.venv/bin/uvicorn app.main:app --port 8010 --reload
# Swagger: http://localhost:8010/docs
```

Swagger에서 인증: `POST /auth/login` 실행 → 응답의 `token` 복사 → 우상단 **Authorize 🔒** 버튼에 붙여넣기 (Bearer 접두어 불필요). 이후 모든 요청에 자동 첨부된다.

기본 DB는 SQLite(`sokmaeum.db`). Postgres 전환: `docker compose up -d` 후
`DATABASE_URL=postgresql+psycopg2://sokmaeum:sokmaeum@localhost:5432/sokmaeum` (+ `pip install psycopg2-binary`).
스키마 변경 시 마이그레이션 없음 — DB 지우고 재시드.

## 데모 계정 (비밀번호 전부 `demo1234`)

| 계정 | 용도 |
|---|---|
| `기요` | 데모 주인공 — 시연 영상 메모 보유 (배지 4곳 + 레파레 넛지), 느끼함 프로필 활성(62점) |
| `맵찔이대학생` | 매운맛 39점(티어1) — "순한맛(1단계) 안전" 가이드 + 입맛 이웃 9명 경로 |
| `헬스인` `야근족` `자취5년차` `아기엄마` `매운맛마니아` | 페르소나 (프로필 대비용) |
| `사장님1`~`사장님5` | 가게 1~5 사장 — 익명 집계 대시보드 |

## API ↔ 데모 시나리오

| API | 화면 |
|---|---|
| `POST /auth/login` `GET /auth/me` | 로그인 (JWT) |
| `POST /memos` | STEP 1 속마음 작성 — 응답 `profile_delta`로 "수집 중 → N점" 연출 |
| `GET /memos` `DELETE /memos/{id}` | 내 메모 목록 / 삭제(프로필 전파) |
| `GET /me/profile` | 입맛 프로필 카드 (n<3 축은 `score:null` = "수집 중") |
| `GET /stores?ordered_only=` | STEP 2 리스트 + 내 속마음 배지 소환 |
| `GET /stores/{id}` | STEP 3-A 재주문 넛지 / 3-B 첫방문 가이드 + 추천 질문 칩 |
| `POST /chat` | STEP 3-C 챗봇 — 이웃 우선 집계, 부족 시 전체 폴백·게이트(≤2 비공개 / 3~4 참고용) |
| `GET /owner/stores/{id}/dashboard` | 사장님 익명 집계 (원문 미노출) |

## 데모 대본 (curl)

```bash
TOKEN=$(curl -s localhost:8010/auth/login -H 'Content-Type: application/json' \
  -d '{"nickname":"기요","password":"demo1234"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
H="Authorization: Bearer $TOKEN"

curl -s "localhost:8010/stores?ordered_only=true" -H "$H"   # ② 배지 소환
curl -s localhost:8010/stores/5 -H "$H"                      # ③-A 레파레 넛지(양파 토핑)
curl -s localhost:8010/stores/6 -H "$H"                      # ③-B "매운맛 12건 중 7건"
curl -s localhost:8010/chat -H "$H" -H 'Content-Type: application/json' \
  -d '{"store_id":5,"question":"버터갈릭 쉬림프 피자가 너무 느끼하진 않을까?"}'  # ③-C "15건 중 9건 만족"
curl -s localhost:8010/memos -H "$H" -H 'Content-Type: application/json' \
  -d '{"store_id":1,"menu_ids":[1],"emotion":"dislike","text":"너무 음식이 달다.. 재주문은 안할듯 ㅠ","chips":["너무 매워요","너무 달아요","비싼 것 같아요"]}'
# ① 작성 → profile_delta: spicy/sweet null→점수 활성화, reorder_intent: neg
```

## 구조

```
app/taxonomy.py        # 17종 어휘·스코프 화이트리스트·칩 매핑·키워드 시드 사전
app/profile_engine.py  # 순수 함수: S(25/50/75)→target→prior50·반감기90d→티어 / 식감 민감도 / L1 이웃
app/services.py        # 검증 관문·이중 저장·재계산·집계(서버가 셈)·게이트·가이드/챗 빌더
app/ai/                # base(Protocol) · static_ai(현재) · upstage_ai(예정)
seed/run_seed.py       # 시연 영상 재현 시드 (수치 고정: 12/7, 15/9)
tests/                 # 프로필 엔진 검증 (문서 §4 "3건→48점" 픽스처 포함)
```

## PoC 단순화 (알고 갈 것)

- 주문 이력 = 메모 이력으로 대체 (주문 엔티티 없음)
- 인증은 시연용 (sha256+salt, 실서비스 아님) / 마이그레이션 없음(create_all)
- static 태거는 감정 토글을 극성으로 사용 — 반어("하나도 안 매움 ㅋㅋ 실망")는 LLM 전환 후 해결
- 프로필 점수 이론상 범위 ≈ 25~75 (프라이어 구조상 극단 티어 0/4는 실질 미도달) — 튜닝 항목
