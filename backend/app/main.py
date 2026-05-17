from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    auth,
    chat,
    conversations,
    health,
    memories,
    messages,
    prompt_templates,
    projects,
    providers,
    settings,
    shares,
    uploads,
)
from app.core.config import settings as app_settings
from app.core.startup import ensure_runtime_schema


app = FastAPI(
    title=app_settings.app_name,
    version="0.1.0",
)

ensure_runtime_schema()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix="/api")
app.include_router(conversations.router, prefix="/api")
app.include_router(memories.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(prompt_templates.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(shares.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(providers.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "ai_web_studio backend is running"}
