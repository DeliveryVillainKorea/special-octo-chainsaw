from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import Base, engine
from .routers import auth_router, chat_router, me_router, memo_router, owner_router, store_router

app = FastAPI(
    title="속마음 리뷰 API (요기요 해커톤 PoC)",
    description="비공개 속마음 메모 → 입맛 프로필 → 개인화 가이드/챗봇. "
                "LLM_PROVIDER=static(규칙+템플릿) | upstage(챗봇만 solar-pro3, 실패 시 static 폴백).",
    version="0.1.0",
)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

# PoC: 마이그레이션 대신 create_all (스키마 바뀌면 DB 파일 지우고 재시드)
Base.metadata.create_all(engine)

app.include_router(auth_router.router)
app.include_router(me_router.router)
app.include_router(memo_router.router)
app.include_router(store_router.router)
app.include_router(chat_router.router)
app.include_router(owner_router.router)


@app.get("/")
def health():
    return {"service": "sokmaeum-review", "status": "ok", "docs": "/docs"}
