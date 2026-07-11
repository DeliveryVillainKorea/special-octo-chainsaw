import os
from datetime import datetime, timezone
from pathlib import Path

# backend/.env 간이 로더 (의존성 없이) — 이미 설정된 env가 우선
_env_file = Path(__file__).resolve().parents[1] / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# 기본은 SQLite(즉시 실행). Postgres 전환: DATABASE_URL=postgresql+psycopg2://sokmaeum:sokmaeum@localhost:5432/sokmaeum
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sokmaeum.db")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
JWT_ALGO = "HS256"
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "72"))

# LLM_PROVIDER=static | upstage
#   static  — 규칙 태거 + 템플릿 (LLM 0회)
#   upstage — 챗봇 경로(질문분류·답변 합성)만 solar-pro3, 태깅·넛지는 static 유지.
#             호출 실패/키 미설정 시 자동 static 폴백.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "static")
UPSTAGE_API_KEY = os.environ.get("UPSTAGE_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.upstage.ai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "solar-pro3")

# 집계 기본 기간(개월) — "최근 6개월, 관련 메모 N건 기준" 표기의 근거
STATS_WINDOW_MONTHS = 6


def utcnow() -> datetime:
    """naive UTC — DB 저장/계산 전부 이 기준으로 통일."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
