from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.auth.dependencies import get_current_user
from src.modules.expenses import crud, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/expenses",
    tags=["Expenses"],
    dependencies=[Depends(require_operations_access)],
)


@router.post("/", response_model=schemas.ExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(
    expense_data: schemas.ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.create_expense(
        db=db, expense=expense_data, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.get("/", response_model=List[schemas.ExpenseResponse])
def read_expenses(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve the list of expenses securely
    return crud.get_expenses(db=db, tenant_id=current_user.tenant_id, skip=skip, limit=limit)


@router.get("/{expense_id}", response_model=schemas.ExpenseResponse)
def read_expense(
    expense_id: int,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    # Retrieve a specific expense by ID securely
    expense = crud.get_expense_by_id(db=db, expense_id=expense_id, tenant_id=current_user.tenant_id)
    if not expense:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found.")
    return expense
