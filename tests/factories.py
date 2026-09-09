import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from src.modules.customers.models import Customer
from src.modules.inventory.models import Product, ProductBatch
from src.modules.purchases.models import Supplier
from src.modules.shifts.models import Shift
from src.modules.tenants.models import Tenant
from src.modules.users.models import User


@dataclass
class PharmacyFactory:
    db: Session
    namespace: str = "synthetic"
    _sequence: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        normalized = re.sub(r"[^a-z0-9-]+", "-", self.namespace.lower()).strip("-")
        self.namespace = (normalized or "synthetic")[:32]

    def _next_key(self, entity: str) -> str:
        self._sequence += 1
        return f"{self.namespace}-{entity}-{self._sequence}"

    def tenant(
        self,
        *,
        name: str | None = None,
        owner_email: str | None = None,
        is_active: bool = False,
    ) -> Tenant:
        if is_active:
            raise ValueError("Create an owner before activating a synthetic tenant")
        key = self._next_key("tenant")
        tenant = Tenant(
            name=name or f"Synthetic Pharmacy {key}",
            owner_email=owner_email or f"{key}@example.test",
            is_active=is_active,
        )
        self.db.add(tenant)
        self.db.flush()
        return tenant

    def owned_tenant(
        self,
        *,
        name: str | None = None,
        owner_email: str | None = None,
        owner_full_name: str | None = None,
        hashed_password: str = "synthetic-password-hash",
        is_active: bool = True,
    ) -> tuple[Tenant, User]:
        tenant = self.tenant(name=name, owner_email=owner_email)
        owner = self.user(
            tenant,
            role="manager",
            email=owner_email,
            full_name=owner_full_name,
            hashed_password=hashed_password,
            owner=True,
        )
        tenant.is_active = is_active
        self.db.flush()
        return tenant, owner

    def user(
        self,
        tenant: Tenant,
        *,
        role: str = "pharmacist",
        email: str | None = None,
        full_name: str | None = None,
        hashed_password: str = "synthetic-password-hash",
        is_active: bool = True,
        owner: bool = False,
    ) -> User:
        if tenant.id is None:
            raise ValueError("Tenant must be persisted before creating a user")
        if owner and role != "manager":
            raise ValueError("A synthetic tenant owner must have the manager role")

        key = self._next_key("user")
        user = User(
            email=email or f"{key}@example.test",
            hashed_password=hashed_password,
            full_name=full_name or f"Synthetic User {key}",
            role=role,
            is_active=is_active,
            tenant_id=tenant.id,
        )
        self.db.add(user)
        self.db.flush()
        if owner:
            tenant.owner_user_id = user.id
            tenant.owner_email = user.email
            self.db.flush()
        return user

    def product(
        self,
        tenant: Tenant,
        *,
        name: str | None = None,
        price: Decimal = Decimal("10.00"),
        total_parts: int = 0,
        reorder_point_parts: int | None = None,
        target_stock_parts: int | None = None,
        is_divisible: bool = False,
        parts_per_unit: int = 1,
    ) -> Product:
        if tenant.id is None:
            raise ValueError("Tenant must be persisted before creating a product")
        key = self._next_key("product")
        product = Product(
            name=name or f"Synthetic Product {key}",
            price=price,
            total_parts=total_parts,
            reorder_point_parts=reorder_point_parts,
            target_stock_parts=target_stock_parts,
            is_divisible=is_divisible,
            parts_per_unit=parts_per_unit,
            tenant_id=tenant.id,
        )
        self.db.add(product)
        self.db.flush()
        return product

    def batch(
        self,
        product: Product,
        *,
        batch_number: str | None = None,
        expiry_date: date = date(2099, 12, 31),
        quantity: int = 0,
    ) -> ProductBatch:
        if product.id is None or product.tenant_id is None:
            raise ValueError("Product must be persisted before creating a batch")
        key = self._next_key("batch")
        batch = ProductBatch(
            batch_number=batch_number or key.upper(),
            expiry_date=expiry_date,
            quantity=quantity,
            product_id=product.id,
            tenant_id=product.tenant_id,
        )
        self.db.add(batch)
        self.db.flush()
        return batch

    def shift(
        self,
        user: User,
        *,
        opening_balance: Decimal = Decimal("0.00"),
    ) -> Shift:
        if user.id is None or user.tenant_id is None:
            raise ValueError("User must be persisted before opening a shift")
        shift = Shift(
            opening_balance=opening_balance,
            user_id=user.id,
            tenant_id=user.tenant_id,
        )
        self.db.add(shift)
        self.db.flush()
        return shift

    def customer(
        self,
        tenant: Tenant,
        *,
        name: str | None = None,
        credit_limit: Decimal = Decimal("0.00"),
    ) -> Customer:
        if tenant.id is None:
            raise ValueError("Tenant must be persisted before creating a customer")
        key = self._next_key("customer")
        customer = Customer(
            name=name or f"Synthetic Customer {key}",
            credit_limit=credit_limit,
            tenant_id=tenant.id,
        )
        self.db.add(customer)
        self.db.flush()
        return customer

    def supplier(self, tenant: Tenant, *, name: str | None = None) -> Supplier:
        if tenant.id is None:
            raise ValueError("Tenant must be persisted before creating a supplier")
        key = self._next_key("supplier")
        supplier = Supplier(
            name=name or f"Synthetic Supplier {key}",
            tenant_id=tenant.id,
        )
        self.db.add(supplier)
        self.db.flush()
        return supplier
