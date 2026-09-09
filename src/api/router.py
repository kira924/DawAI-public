from fastapi import APIRouter

from src.modules.auth.router import router as auth_router
from src.modules.catalog.router import router as catalog_router
from src.modules.customers.router import router as customers_router
from src.modules.expenses.router import router as expenses_router
from src.modules.finance.router import router as finance_router
from src.modules.inventory.router import router as inventory_router
from src.modules.purchases.router import router as purchases_router
from src.modules.reports.router import router as reports_router
from src.modules.sales.router import router as sales_router
from src.modules.shifts.router import router as shifts_router
from src.modules.tenants.router import router as tenants_router
from src.modules.users.router import router as users_router

api_router = APIRouter()
api_router.include_router(tenants_router)
api_router.include_router(users_router)
api_router.include_router(auth_router)
api_router.include_router(catalog_router)
api_router.include_router(inventory_router)
api_router.include_router(sales_router)
api_router.include_router(purchases_router)
api_router.include_router(expenses_router)
api_router.include_router(finance_router)
api_router.include_router(shifts_router)
api_router.include_router(customers_router)
api_router.include_router(reports_router)
