from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe")
async def ready() -> dict:
    return {"status": "ready"}
