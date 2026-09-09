from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.auth.dependencies import get_current_user
from src.modules.purchases import crud, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/purchases",
    tags=["Purchases & Suppliers"],
    dependencies=[Depends(require_operations_access)],
)


# Supplier Endpoints
@router.post(
    "/suppliers/", response_model=schemas.SupplierResponse, status_code=status.HTTP_201_CREATED
)
def create_supplier(
    supplier: schemas.SupplierCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Securely pass the tenant_id from the authenticated user
    return crud.create_supplier(db=db, supplier=supplier, tenant_id=current_user.tenant_id)


@router.get("/suppliers/", response_model=List[schemas.SupplierResponse])
def read_suppliers(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.get_suppliers(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


@router.get("/suppliers/{supplier_id}", response_model=schemas.SupplierResponse)
def read_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve a specific supplier by ID securely
    return crud.get_supplier_by_id(db=db, supplier_id=supplier_id, tenant_id=current_user.tenant_id)


@router.patch("/suppliers/{supplier_id}", response_model=schemas.SupplierResponse)
def update_supplier(
    supplier_id: int,
    supplier_update: schemas.SupplierUpdate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Update supplier details securely
    return crud.update_supplier(
        db=db,
        supplier_id=supplier_id,
        supplier_update=supplier_update,
        tenant_id=current_user.tenant_id,
    )


# Purchase Order Endpoints
@router.post(
    "/orders/", response_model=schemas.PurchaseOrderResponse, status_code=status.HTTP_201_CREATED
)
def create_purchase_order(
    order: schemas.PurchaseOrderCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Inject both user_id and tenant_id securely from the token
    return crud.create_purchase_order(
        db=db, order=order, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.get("/orders/", response_model=List[schemas.PurchaseOrderResponse])
def read_purchase_orders(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.get_purchase_orders(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


@router.get("/orders/{order_id}", response_model=schemas.PurchaseOrderResponse)
def read_purchase_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve a specific purchase order with all its items
    return crud.get_purchase_order_by_id(db=db, order_id=order_id, tenant_id=current_user.tenant_id)


# Endpoint for confirming receipt of goods
@router.post("/orders/{order_id}/receive", response_model=schemas.PurchaseOrderResponse)
def receive_order(
    order_id: int,
    receipt: schemas.PurchaseReceiptCreate | None = None,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # This endpoint triggers the actual inventory addition
    return crud.receive_purchase_order(
        db=db,
        order_id=order_id,
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        actor_role=current_user.role,
        confirm_near_expiry=receipt.confirm_near_expiry if receipt else False,
    )


# Endpoint for cancelling a pending purchase order
@router.post("/orders/{order_id}/cancel", response_model=schemas.PurchaseOrderResponse)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # This endpoint updates the status to CANCELLED and prevents future receiving
    return crud.cancel_purchase_order(db=db, order_id=order_id, tenant_id=current_user.tenant_id)


# Supplier Payments Endpoints
@router.post(
    "/payments/",
    response_model=schemas.SupplierPaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_payment(
    payment: schemas.SupplierPaymentCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Securely process the payment with the authenticated user context
    return crud.create_supplier_payment(
        db=db, payment=payment, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.get("/payments/", response_model=List[schemas.SupplierPaymentResponse])
def read_payments(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve the ledger of all outgoing payments
    return crud.get_supplier_payments(
        db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )


# Purchase Returns Endpoints
@router.post(
    "/returns/", response_model=schemas.PurchaseReturnResponse, status_code=status.HTTP_201_CREATED
)
def create_purchase_return(
    return_data: schemas.PurchaseReturnCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Securely process the return and deduct inventory
    return crud.create_purchase_return(
        db=db, return_data=return_data, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.get("/returns/", response_model=List[schemas.PurchaseReturnResponse])
def read_purchase_returns(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve the history of all returned products
    return crud.get_purchase_returns(
        db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit
    )


@router.get("/suppliers/{supplier_id}/balance", response_model=schemas.SupplierBalanceResponse)
def read_supplier_balance(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # This generates a real-time financial statement for the supplier
    return crud.get_supplier_balance(
        db=db, supplier_id=supplier_id, tenant_id=current_user.tenant_id
    )
