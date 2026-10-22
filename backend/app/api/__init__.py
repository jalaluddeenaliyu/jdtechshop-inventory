from fastapi import APIRouter

from app.api import (
    auth,
    catalog,
    customers,
    debts,
    expenses,
    health,
    product_import,
    products,
    purchasing,
    quick_sales,
    reports,
    returns,
    sales,
    settings,
    stock,
    units,
    users,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(settings.router)
api_router.include_router(catalog.router)
api_router.include_router(product_import.router)
api_router.include_router(products.router)
api_router.include_router(stock.router)
api_router.include_router(units.router)
api_router.include_router(purchasing.router)
api_router.include_router(customers.router)
api_router.include_router(sales.router)
api_router.include_router(quick_sales.router)
api_router.include_router(debts.router)
api_router.include_router(returns.router)
api_router.include_router(expenses.router)
api_router.include_router(reports.router)
