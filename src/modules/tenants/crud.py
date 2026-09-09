from sqlalchemy.orm import Session

from src.modules.tenants import models, schemas


# Function to create a new tenant
def create_tenant(db: Session, tenant: schemas.TenantCreate):
    db_tenant = models.Tenant(
        name=tenant.name,
        owner_email=tenant.owner_email,
        is_active=False,
    )
    db.add(db_tenant)
    db.commit()
    db.refresh(db_tenant)
    return db_tenant


# Function to get a single tenant by their ID
def get_tenant(db: Session, tenant_id: int):
    return db.query(models.Tenant).filter(models.Tenant.id == tenant_id).first()


# Function to get a list of tenants with pagination
def get_tenants(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Tenant).order_by(models.Tenant.id.asc()).offset(skip).limit(limit).all()
