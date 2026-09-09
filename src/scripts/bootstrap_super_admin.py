import argparse
import getpass

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from src.core.database import sessionLocal
from src.core.security import get_password_hash, normalize_email
from src.modules.tenants.models import Tenant
from src.modules.users.models import User

PLATFORM_TENANT_NAME = "DawAI Platform (Internal)"


def bootstrap_super_admin(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
) -> User:
    normalized_email = normalize_email(str(TypeAdapter(EmailStr).validate_python(email)))
    normalized_name = full_name.strip()
    if not normalized_name:
        raise ValueError("Full name must not be blank")
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Password must contain between 12 and 128 characters")

    if db.query(User.id).filter(User.role == "super_admin").first() is not None:
        raise RuntimeError("A super_admin already exists; bootstrap is permanently disabled")
    if db.query(User.id).filter(User.email.ilike(normalized_email)).first() is not None:
        raise RuntimeError("A user with this email already exists")

    platform_tenant = (
        db.query(Tenant).filter(Tenant.name == PLATFORM_TENANT_NAME).with_for_update().first()
    )
    if platform_tenant is None:
        platform_tenant = Tenant(
            name=PLATFORM_TENANT_NAME,
            owner_email=normalized_email,
            owner_user_id=None,
            subscription_plan="Internal",
            is_active=False,
        )
        db.add(platform_tenant)
        db.flush()
    elif platform_tenant.is_active or platform_tenant.owner_user_id is not None:
        raise RuntimeError("The internal platform tenant is not eligible for bootstrap")

    user = User(
        email=normalized_email,
        hashed_password=get_password_hash(password),
        full_name=normalized_name,
        role="super_admin",
        is_active=True,
        tenant_id=platform_tenant.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first DawAI super administrator")
    parser.add_argument("--email", required=True)
    parser.add_argument("--full-name", required=True)
    arguments = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match")

    try:
        with sessionLocal() as db:
            user = bootstrap_super_admin(
                db,
                email=arguments.email,
                full_name=arguments.full_name,
                password=password,
            )
            print(f"Created super_admin user {user.email}")
    except (RuntimeError, ValueError, ValidationError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
