from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.security import get_password_hash, normalize_email
from src.modules.users import models, schemas


# Function to get a user by their email
def get_user_by_email(db: Session, email: str):
    normalized_email = normalize_email(email)
    return db.query(models.User).filter(func.lower(models.User.email) == normalized_email).first()


# Function to get a user by their ID
def get_user(db: Session, user_id: int):
    return db.query(models.User).filter(models.User.id == user_id).first()


# Function to create a new user and assign them to a tenant
def create_user(db: Session, user: schemas.UserCreate):
    # 1. Hash the plain password
    hashed_pwd = get_password_hash(user.password)

    # 2. Map the data to the SQLAlchemy model
    # Note: We skip user.password and use hashed_pwd instead
    db_user = models.User(
        email=normalize_email(str(user.email)),
        hashed_password=hashed_pwd,
        full_name=user.full_name,
        role=user.role.value,
        is_active=user.is_active,
        tenant_id=user.tenant_id,
    )

    # 3. Save to the database
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    return db_user


# Function to update an existing user
def update_user(db: Session, db_user: models.User, user_update: schemas.UserUpdate):
    update_data = user_update.model_dump(exclude_unset=True, mode="json")

    for key, value in update_data.items():
        setattr(db_user, key, value)

    db.commit()
    db.refresh(db_user)

    return db_user
