# special-octo-chainsaw

속마음 리뷰 AI — 요기요 해커톤 PoC

> 공개 리뷰 옆에 나만 보는 "속마음 메모"를 남기면, AI가 태깅(17종 닫힌 어휘) →
> 입맛 프로필(5축 점수) → 재주문 넛지 / 첫주문 가이드 / 챗봇 개인화 답변으로 돌려주는 서비스.

## 구성

```
backend/                  # FastAPI 백엔드 (상세: backend/README.md)
속마음리뷰_태깅체계.html    # 태깅 체계 리서치 문서
```

## 백엔드 실행 방법

요구사항: **Python 3.10+** (3.9 불가 — `X | None` 타입 문법 사용)

```bash
cd backend

# 1) 가상환경 + 의존성
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) 시드 (DB 초기화 + 시연 데이터 — 가게 6곳, 계정 25개, 익명 풀 30명)
.venv/bin/python -m seed.run_seed

# 3) 서버 기동
.venv/bin/uvicorn app.main:app --port 8010 --reload
```

- Swagger UI: http://localhost:8010/docs
- 헬스체크: `curl localhost:8010/`
- 테스트: `.venv/bin/python -m pytest tests/ -q`

### 로그인 (데모 계정 — 비밀번호 전부 `demo1234`)

```bash
curl -s localhost:8010/auth/login -H 'Content-Type: application/json' \
  -d '{"nickname":"기요","password":"demo1234"}'
# 응답의 token을 이후 요청에 사용: -H "Authorization: Bearer <token>"
# Swagger에서는 우상단 Authorize 🔒 버튼에 token만 붙여넣으면 됨 (Bearer 접두어 불필요)
```

| 계정 | 용도 |
|---|---|
| `기요` | 데모 주인공 (배지·넛지·챗봇 시나리오) |
| `맵찔이대학생` | 매운맛 티어1 — "순한맛(1단계) 안전" 가이드 + 입맛 이웃 경로 |
| `사장님1`~`사장님5` | 사장님 대시보드 (익명 집계) |

### DB 전환 (기본 SQLite → Postgres)

```bash
cd backend
docker compose up -d
.venv/bin/pip install psycopg2-binary
export DATABASE_URL=postgresql+psycopg2://sokmaeum:sokmaeum@localhost:5432/sokmaeum
.venv/bin/python -m seed.run_seed && .venv/bin/uvicorn app.main:app --port 8010
```

스키마 변경 시 마이그레이션 없음 — DB 지우고(`rm backend/sokmaeum.db`) 재시드.

### 주의

- **AI는 현재 static 모드** (`LLM_PROVIDER=static`): 규칙 태거 + 템플릿 문장. LLM(Upstage solar-pro3) 연동은 튜닝 후 `backend/app/ai/upstage_ai.py` 구현으로 전환 예정.
- 8000 포트 대신 8010을 쓰는 이유: 로컬에서 8000을 다른 프로세스가 자주 점유.

데모 대본(curl 시나리오), API 표, 아키텍처 설명은 [backend/README.md](backend/README.md) 참고.
