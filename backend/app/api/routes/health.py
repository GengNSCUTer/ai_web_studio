from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.health_service import readiness, worker_readiness

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/health/live")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "time": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/ready")
async def readiness_check() -> JSONResponse:
    result = await readiness()
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503,
                        headers={"Cache-Control": "no-store"})


@router.get("/health/workers")
async def worker_readiness_check() -> JSONResponse:
    result = await worker_readiness()
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503,
                        headers={"Cache-Control": "no-store"})
