from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.auth.dependencies import get_current_user
from src.modules.customers import crud, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/customers",
    tags=["Customers & Debts"],
    dependencies=[Depends(require_operations_access)],
)


@router.post("/", response_model=schemas.CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer(
    customer_data: schemas.CustomerCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.create_customer(db=db, customer=customer_data, tenant_id=current_user.tenant_id)


@router.get("/", response_model=List[schemas.CustomerResponse])
def read_customers(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.get_customers(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


@router.post(
    "/payments", response_model=schemas.CustomerPaymentResponse, status_code=status.HTTP_201_CREATED
)
def create_payment(
    payment_data: schemas.CustomerPaymentCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # This will process the payment and put money into the active shift
    return crud.create_customer_payment(
        db=db, payment=payment_data, user_id=current_user.id, tenant_id=current_user.tenant_id
    )
