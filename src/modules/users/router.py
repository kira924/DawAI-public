from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.pagination import PageLimit, PageOffset
from src.core.database import get_db
from src.modules.auth import schemas as auth_schemas
from src.modules.auth import service as auth_service
from src.modules.auth.authorization import require_user_administration
from src.modules.auth.dependencies import get_current_user
from src.modules.auth.notifications import (
    EmailDeliveryUnavailable,
    ensure_email_delivery_configured,
)
from src.modules.users import crud, schemas, service
from src.modules.users.models import User

router = APIRouter(
    prefix="/users",
    tags=["Users (Pharmacists & Managers)"],
    dependencies=[Depends(require_user_administration)],
)


# 1. Create a new user
@router.post("/", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user: schemas.UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.ensure_can_create_user(db, current_user, user)

    db_user = crud.get_user_by_email(db, email=user.email)
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered in the system.")

    return crud.create_user(db=db, user=user)


# 2. Get a list of users
@router.get("/", response_model=List[schemas.UserResponse])
def read_users(
    skip: PageOffset = 0,
    limit: PageLimit = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        service.visible_users_query(db, current_user)
        .order_by(User.id.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


# 3. Get a single user by ID
@router.get("/{user_id}", response_model=schemas.UserResponse)
def read_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_user = service.get_visible_user(db, current_user, user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return db_user


# 4. Update user
@router.patch("/{user_id}", response_model=schemas.UserResponse)
def update_user(
    user_id: int,
    user_update: schemas.UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    db_user = service.get_administrable_user(db, current_user, user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    service.ensure_update_is_allowed(db, current_user, db_user, user_update)
    return crud.update_user(db=db, db_user=db_user, user_update=user_update)


@router.post("/{user_id}/password-reset", response_model=auth_schemas.StatusMessage)
def initiate_user_password_reset(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target = service.get_administrable_user(db, current_user, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    try:
        ensure_email_delivery_configured()
        auth_service.issue_password_reset(
            db,
            target,
            requested_by_user_id=current_user.id,
        )
    except EmailDeliveryUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Account email delivery is temporarily unavailable",
        )
    return {"detail": "If the account is eligible, a reset link has been sent"}
