from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.catalog import schemas, service

router = APIRouter(
    prefix="/catalog",
    tags=["Medicine Catalog"],
    dependencies=[Depends(require_operations_access)],
)


@router.get("/products/search", response_model=list[schemas.CatalogProductResponse])
def search_products(
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return service.search_catalog_products(db, q, limit)
