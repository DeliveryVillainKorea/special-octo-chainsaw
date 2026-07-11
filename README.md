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

# 2) PostgreSQL 기동 (yogiyo DB — 이미 5433에 PG를 띄웠다면 생략)
docker compose up -d

# 3) 시드 (DB 초기화 + 시연 데이터 — 가게 6곳, 계정 25개, 익명 풀 30명)
.venv/bin/python -m seed.run_seed

# 4) 서버 기동
.venv/bin/uvicorn app.main:app --port 8010 --reload
```

DB 연결은 `backend/.env`의 `DATABASE_URL`이 결정한다 (없으면 SQLite 폴백):
`postgresql+psycopg2://root:0000@localhost:5433/yogiyo`

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

### DB 관리

- 스키마 변경 시 마이그레이션 없음 — 재시드(`python -m seed.run_seed`)가 테이블을 재생성한다.
- DB 완전 초기화: `docker compose down -v` 후 다시 `up -d` + 재시드.
- Docker 없이 돌리기: `backend/.env`의 `DATABASE_URL` 줄을 지우면 SQLite(`sokmaeum.db`)로 폴백.

### 주의

- **AI 기본은 static 모드** (규칙 태거 + 템플릿). **챗봇을 Upstage solar-pro3로 켜려면**: `cd backend && cp .env.example .env` 후 `UPSTAGE_API_KEY` 입력, 서버 재시작. 실패 시 자동 static 폴백이라 데모 안전.
- 8000 포트 대신 8010을 쓰는 이유: 로컬에서 8000을 다른 프로세스가 자주 점유.

데모 대본(curl 시나리오), API 표, 아키텍처 설명은 [backend/README.md](backend/README.md) 참고.
