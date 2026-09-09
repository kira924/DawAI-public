from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.modules.auth.authorization import require_operations_access
from src.modules.auth.dependencies import get_current_user
from src.modules.shifts import crud, schemas
from src.modules.users import schemas as user_schemas

router = APIRouter(
    prefix="/shifts",
    tags=["Shifts & Cash Drawer"],
    dependencies=[Depends(require_operations_access)],
)


@router.post("/open", response_model=schemas.ShiftResponse)
def open_shift(
    shift_data: schemas.ShiftCreate,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.open_shift(
        db=db, shift=shift_data, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.post("/close", response_model=schemas.ShiftResponse)
def close_shift(
    shift_data: schemas.ShiftClose,
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    return crud.close_shift(
        db=db, shift_close=shift_data, user_id=current_user.id, tenant_id=current_user.tenant_id
    )


@router.get("/active", response_model=schemas.ShiftResponse)
def get_current_active_shift(
    db: Session = Depends(get_db),
    current_user: user_schemas.UserResponse = Depends(get_current_user),
):
    active_shift = crud.get_active_shift(
        db=db, user_id=current_user.id, tenant_id=current_user.tenant_id
    )
    if not active_shift:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="You do not have an active shift."
        )
    return active_shift
