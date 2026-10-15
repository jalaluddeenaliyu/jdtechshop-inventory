"""Import every model here so Alembic and the app see the full schema."""
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.catalog import Brand, Category, Condition, Product, TrackingType
from app.models.customers import WALK_IN_NAME, Customer
from app.models.expenses import Expense
from app.models.purchasing import (
    Purchase,
    PurchaseLine,
    PurchaseLineUnit,
    PurchaseStatus,
    Supplier,
    SupplierPayment,
)
from app.models.returns import (
    AdjustmentStatus,
    SaleReturn,
    SaleReturnLine,
    StockAdjustment,
    StockAdjustmentLine,
    SupplierReturn,
    SupplierReturnLine,
)
from app.models.sales import (
    CustomerPayment,
    PaymentAllocation,
    ReceiptCounter,
    Sale,
    SaleLine,
    SaleStatus,
)
from app.models.settings import BusinessSettings, PaymentMethod
from app.models.stock import MovementType, StockMovement
from app.models.units import ON_HAND_STATUSES, ImeiRegistry, ProductUnit, UnitStatus
from app.models.user import Role, User

__all__ = [
    "Base", "User", "Role", "AuditLog", "BusinessSettings", "PaymentMethod",
    "Category", "Brand", "Product", "TrackingType", "Condition",
    "StockMovement", "MovementType",
    "ProductUnit", "UnitStatus", "ImeiRegistry", "ON_HAND_STATUSES",
    "Supplier", "Purchase", "PurchaseLine", "PurchaseLineUnit", "PurchaseStatus", "SupplierPayment",
    "Customer", "WALK_IN_NAME",
    "Sale", "SaleLine", "SaleStatus", "CustomerPayment", "ReceiptCounter", "PaymentAllocation",
    "SaleReturn", "SaleReturnLine", "SupplierReturn", "SupplierReturnLine",
    "StockAdjustment", "StockAdjustmentLine", "AdjustmentStatus", "Expense",
]
