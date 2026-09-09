from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.auth.dependencies import get_current_user
from src.modules.sales import crud, schemas
from src.modules.users.models import User

router = APIRouter(
    prefix="/sales",
    tags=["Sales & POS"],
    dependencies=[Depends(require_operations_access)],
)


# 1. Create a new invoice (Checkout)
@router.post("/", response_model=schemas.InvoiceResponse, status_code=status.HTTP_201_CREATED)
def create_invoice(
    invoice: schemas.InvoiceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Pass the authenticated user ID (cashier) and tenant ID safely to the CRUD layer
    return crud.create_invoice(
        db=db, invoice=invoice, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


# 2. Get all invoices for the current pharmacy
@router.get("/", response_model=List[schemas.InvoiceResponse])
def read_invoices(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Enforce tenant isolation on reading invoices
    return crud.get_invoices(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


# 3. Get a specific invoice by ID
@router.get("/{invoice_id}", response_model=schemas.InvoiceResponse)
def read_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_invoice = crud.get_invoice_by_id(
        db=db, invoice_id=invoice_id, tenant_id=current_user.tenant_id
    )
    if db_invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    return db_invoice


@router.post(
    "/returns", response_model=schemas.SalesReturnResponse, status_code=status.HTTP_201_CREATED
)
def create_sales_return(
    return_data: schemas.SalesReturnCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Process the return securely using the active shift
    return crud.create_sales_return(
        db=db,
        return_data=return_data,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
        user_role=current_user.role,
    )
