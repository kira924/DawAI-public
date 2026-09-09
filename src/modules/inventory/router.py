from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import (
    require_inventory_control,
    require_operations_access,
    require_reports_access,
)
from src.modules.auth.dependencies import get_current_user
from src.modules.catalog import service as catalog_service
from src.modules.inventory import control, crud, ledger, replenishment, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/products",
    tags=["Inventory & Products"],
    dependencies=[Depends(require_operations_access)],
)


# 1. Create a new product
@router.post("/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: schemas.ProductCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # The tenant_id is automatically extracted from the authenticated user
    # The user never sends it in the request body, ensuring 100% security
    try:
        return crud.create_product(
            db=db,
            product=product,
            tenant_id=current_user.tenant_id,
            actor_role=current_user.role,
            actor_user_id=current_user.id,
        )
    except crud.CatalogProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except crud.CatalogProductAlreadyLinkedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# 2. Get all products for the current pharmacy
@router.get("/", response_model=List[schemas.ProductResponse])
def read_products(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Pass the tenant_id to the CRUD layer to enforce data isolation
    return crud.get_products(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


@router.get("/search", response_model=List[schemas.ProductResponse])
def search_products(
    q: str = Query(min_length=2, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    products = catalog_service.search_tenant_products(db, current_user.tenant_id, q, limit)
    crud.attach_stock_breakdown(db, products)
    return products


@router.get(
    "/replenishment",
    response_model=List[schemas.ReplenishmentRecommendationResponse],
)
def read_replenishment_recommendations(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return replenishment.get_replenishment_recommendations(
        db,
        current_user.tenant_id,
        skip=skip,
        limit=limit,
    )


@router.put(
    "/{product_id}/replenishment-policy",
    response_model=schemas.ReplenishmentPolicyResponse,
    dependencies=[Depends(require_inventory_control)],
)
def set_replenishment_policy(
    product_id: int,
    payload: schemas.ReplenishmentPolicyUpsert,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    policy = replenishment.set_replenishment_policy(
        db,
        product_id,
        current_user.tenant_id,
        payload,
    )
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return policy


@router.get("/stock-movements", response_model=List[schemas.StockMovementResponse])
def read_stock_movements(
    product_id: int | None = Query(default=None, gt=0),
    batch_id: int | None = Query(default=None, gt=0),
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return ledger.get_stock_movements(
        db,
        current_user.tenant_id,
        product_id=product_id,
        batch_id=batch_id,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/stock-reconciliation",
    response_model=schemas.StockReconciliationResponse,
    dependencies=[Depends(require_reports_access)],
)
def read_stock_reconciliation(
    issue_limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return ledger.reconcile_stock(db, current_user.tenant_id, issue_limit=issue_limit)


@router.post(
    "/stock-counts",
    response_model=schemas.StockCountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_stock_count(
    payload: schemas.StockCountCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.create_stock_count(
        db,
        payload,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )


@router.get("/stock-counts", response_model=List[schemas.StockCountResponse])
def read_stock_counts(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.get_stock_counts(db, current_user.tenant_id, skip=skip, limit=limit)


@router.patch("/stock-counts/{count_id}", response_model=schemas.StockCountResponse)
def update_stock_count(
    count_id: int,
    payload: schemas.StockCountUpdate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.update_stock_count(
        db,
        count_id,
        payload,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        user_role=current_user.role,
    )


@router.post("/stock-counts/{count_id}/submit", response_model=schemas.StockCountResponse)
def submit_stock_count(
    count_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.submit_stock_count(
        db,
        count_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        user_role=current_user.role,
    )


@router.post("/stock-counts/{count_id}/cancel", response_model=schemas.StockCountResponse)
def cancel_stock_count(
    count_id: int,
    payload: schemas.StockCountCancel,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.cancel_stock_count(
        db,
        count_id,
        payload,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        user_role=current_user.role,
    )


@router.post(
    "/stock-counts/{count_id}/apply",
    response_model=schemas.StockCountResponse,
    dependencies=[Depends(require_inventory_control)],
)
def apply_stock_count(
    count_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.apply_stock_count(
        db,
        count_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        user_role=current_user.role,
    )


@router.post(
    "/quarantine",
    response_model=schemas.QuarantineItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def quarantine_stock(
    payload: schemas.QuarantineCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.create_quarantine_item(
        db,
        payload,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
    )


@router.get("/quarantine", response_model=List[schemas.QuarantineItemResponse])
def read_quarantine_items(
    include_resolved: bool = False,
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.get_quarantine_items(
        db,
        current_user.tenant_id,
        include_resolved=include_resolved,
        skip=skip,
        limit=limit,
    )


@router.post(
    "/quarantine/{quarantine_item_id}/dispositions",
    response_model=schemas.QuarantineDispositionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_inventory_control)],
)
def create_quarantine_disposition(
    quarantine_item_id: int,
    payload: schemas.QuarantineDispositionCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return control.create_quarantine_disposition(
        db,
        quarantine_item_id,
        payload,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        user_role=current_user.role,
    )


# 3. Get a specific product by ID
@router.get("/{product_id}", response_model=schemas.ProductResponse)
def read_product(
    product_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    db_product = crud.get_product(db=db, product_id=product_id, tenant_id=current_user.tenant_id)
    if db_product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    return db_product


# 4. Update a product
@router.patch("/{product_id}", response_model=schemas.ProductResponse)
def update_product(
    product_id: int,
    product_update: schemas.ProductUpdate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # First, verify the product exists and belongs to this tenant
    db_product = crud.get_product(db=db, product_id=product_id, tenant_id=current_user.tenant_id)
    if db_product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    try:
        return crud.update_product(db=db, db_product=db_product, product_update=product_update)
    except crud.CatalogProductNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except crud.CatalogProductAlreadyLinkedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/batches/", response_model=List[schemas.ProductBatchResponse])
def read_product_batches(
    product_id: Optional[int] = Query(None, description="Filter batches by a specific product ID"),
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve batches, optionally filtered, and ordered by nearest expiry date
    return crud.get_product_batches(
        db=db, tenant_id=current_user.tenant_id, product_id=product_id, skip=skip, limit=limit
    )
