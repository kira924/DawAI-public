from fastapi import Depends, FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.contracts import API_ERROR_RESPONSES, API_V1_PREFIX, LEGACY_API_PREFIX
from src.api.errors import install_exception_handlers
from src.api.middleware import ApiContractMiddleware
from src.api.router import api_router
from src.core.config import settings
from src.core.database import get_db

app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)
app.add_middleware(ApiContractMiddleware)
install_exception_handlers(app)
app.include_router(api_router, prefix=API_V1_PREFIX, responses=API_ERROR_RESPONSES)
app.include_router(api_router, prefix=LEGACY_API_PREFIX, include_in_schema=False)


@app.get("/")
def read_root():
    return {"message": "Welcom to DawAI System"}


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "ready"}
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": "unavailable"},
        )


@app.get("/health/live")
def liveness_check():
    return {"status": "healthy"}


@app.get("/health/ready")
def readiness_check(db: Session = Depends(get_db)):
    return health_check(db)
