"""Categories, brands and products.

Products never get a quantity or cost here — those change only through the stock ledger
(`app.services.stock`). Nothing is deleted: categories, brands and products are turned off.
"""
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core import audit
from app.models import (
    Brand,
    MovementType,
    BusinessSettings,
    Category,
    Condition,
    Product,
    ProductUnit,
    StockMovement,
    TrackingType,
    User,
)
from app.models.catalog import PRODUCT_SKU_SEQ
from app.models.units import ON_HAND_STATUSES
from app.schemas.catalog import (
    BrandCreate,
    BrandUpdate,
    CategoryCreate,
    CategoryOut,
    CategoryUpdate,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)
from app.services.stock import add_opening_stock, has_movements

UNPROCESSABLE = 422  # Starlette renamed this constant; the number is what matters

StockFilter = Literal["low", "out", "in"]
ProductSort = Literal["name", "sku", "price", "qty", "updated"]


def _change(changes: dict, obj, field: str, new) -> None:
    old = getattr(obj, field)
    if old != new:
        changes[field] = {"from": None if old is None else str(old),
                          "to": None if new is None else str(new)}
        setattr(obj, field, new)


def _name_taken(db: Session, model, name: str, exclude_id: int | None = None) -> bool:
    q = select(model.id).where(func.lower(model.name) == name.lower())
    if exclude_id is not None:
        q = q.where(model.id != exclude_id)
    return db.scalar(q) is not None


def like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ---------- Categories ----------

def category_out(c: Category) -> CategoryOut:
    out = CategoryOut.model_validate(c)
    out.parent_name = c.parent.name if c.parent else None
    return out


def _valid_parent(db: Session, parent_id: int | None, category: Category | None) -> None:
    if parent_id is None:
        return
    parent = db.get(Category, parent_id)
    if parent is None:
        raise HTTPException(UNPROCESSABLE, "Parent category not found.")
    if category is not None and parent.id == category.id:
        raise HTTPException(UNPROCESSABLE,
                            "A category cannot be inside itself.")
    if parent.parent_id is not None:
        raise HTTPException(UNPROCESSABLE,
                            f"“{parent.name}” is already a subcategory. "
                            "Subcategories can only be one level deep.")
    if category is not None and db.scalar(
        select(Category.id).where(Category.parent_id == category.id).limit(1)
    ):
        raise HTTPException(UNPROCESSABLE,
                            f"“{category.name}” has its own subcategories, "
                            "so it cannot become a subcategory.")


def create_category(db: Session, data: CategoryCreate, actor: User,
                    request: Request | None) -> Category:
    if _name_taken(db, Category, data.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "A category with that name exists.")
    _valid_parent(db, data.parent_id, None)
    category = Category(name=data.name, parent_id=data.parent_id, is_active=True)
    db.add(category)
    db.flush()
    audit.record(db, action="category.created", user=actor, entity_type="category",
                 entity_id=category.id,
                 details={"name": category.name, "parent_id": category.parent_id},
                 request=request)
    db.commit()
    db.refresh(category)
    return category


def update_category(db: Session, category_id: int, data: CategoryUpdate, actor: User,
                    request: Request | None) -> Category:
    category = db.get(Category, category_id, with_for_update=True)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found.")
    fields = data.model_dump(exclude_unset=True)
    if "name" in fields and _name_taken(db, Category, fields["name"], exclude_id=category.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A category with that name exists.")
    if "parent_id" in fields and fields["parent_id"] != category.parent_id:
        _valid_parent(db, fields["parent_id"], category)
    changes: dict = {}
    for field, new in fields.items():
        _change(changes, category, field, new)
    if not changes:
        db.rollback()
        return category
    audit.record(db, action="category.updated", user=actor, entity_type="category",
                 entity_id=category.id, details=changes, request=request)
    db.commit()
    db.refresh(category)
    return category


def list_categories(db: Session, include_inactive: bool) -> list[Category]:
    q = select(Category).order_by(func.lower(Category.name))
    if not include_inactive:
        q = q.where(Category.is_active.is_(True))
    return list(db.scalars(q))


# ---------- Brands ----------

def create_brand(db: Session, data: BrandCreate, actor: User, request: Request | None) -> Brand:
    if _name_taken(db, Brand, data.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "A brand with that name exists.")
    brand = Brand(name=data.name, is_active=True)
    db.add(brand)
    db.flush()
    audit.record(db, action="brand.created", user=actor, entity_type="brand",
                 entity_id=brand.id, details={"name": brand.name}, request=request)
    db.commit()
    return brand


def update_brand(db: Session, brand_id: int, data: BrandUpdate, actor: User,
                 request: Request | None) -> Brand:
    brand = db.get(Brand, brand_id, with_for_update=True)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Brand not found.")
    fields = data.model_dump(exclude_unset=True)
    if "name" in fields and _name_taken(db, Brand, fields["name"], exclude_id=brand.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A brand with that name exists.")
    changes: dict = {}
    for field, new in fields.items():
        _change(changes, brand, field, new)
    if not changes:
        db.rollback()
        return brand
    audit.record(db, action="brand.updated", user=actor, entity_type="brand",
                 entity_id=brand.id, details=changes, request=request)
    db.commit()
    return brand


def list_brands(db: Session, include_inactive: bool) -> list[Brand]:
    q = select(Brand).order_by(func.lower(Brand.name))
    if not include_inactive:
        q = q.where(Brand.is_active.is_(True))
    return list(db.scalars(q))


# ---------- Products ----------

def default_reorder_level(db: Session) -> int:
    return db.scalar(select(BusinessSettings.default_reorder_level).where(BusinessSettings.id == 1)) or 0


def opening_stock_open(db: Session, products: list[Product]) -> dict[int, bool]:
    """Can opening stock still be entered? Quantity items: only before any movement.
    Serial/IMEI items: until the first movement that is not opening stock."""
    ids = [p.id for p in products]
    if not ids:
        return {}
    moved = set(db.scalars(select(StockMovement.product_id).where(
        StockMovement.product_id.in_(ids)).distinct()))
    beyond_opening = set(db.scalars(select(StockMovement.product_id).where(
        StockMovement.product_id.in_(ids),
        StockMovement.movement_type != MovementType.OPENING).distinct()))
    return {
        p.id: p.is_active and (p.id not in beyond_opening
                               if p.tracking_type == TrackingType.SERIALIZED else p.id not in moved)
        for p in products
    }


def unit_stock_values(db: Session, products: list[Product]) -> dict[int, Decimal]:
    """Sum of the cost of each serial/IMEI unit on hand, per product (same rule as the reports)."""
    ids = [p.id for p in products if p.tracking_type == TrackingType.SERIALIZED]
    if not ids:
        return {}
    return dict(db.execute(
        select(ProductUnit.product_id, func.sum(ProductUnit.unit_cost))
        .where(ProductUnit.product_id.in_(ids), ProductUnit.status.in_(ON_HAND_STATUSES))
        .group_by(ProductUnit.product_id)).all())


def _valuation(p: Product, unit_values: dict[int, Decimal]) -> tuple[Decimal, Decimal | None]:
    if p.tracking_type == TrackingType.SERIALIZED:
        value = Decimal(unit_values.get(p.id) or 0)
    else:
        value = Decimal(p.qty_on_hand) * (p.avg_cost or 0)
    value = value.quantize(Decimal("0.01"))
    if p.qty_on_hand > 0:
        each = (value / p.qty_on_hand).quantize(Decimal("0.01"))
    else:
        each = p.avg_cost if p.avg_cost and p.avg_cost > 0 else None
    return value, each


def product_out_one(db: Session, p: Product, show_cost: bool) -> ProductOut:
    return product_out(p, default_reorder_level(db), show_cost, opening_stock_open(db, [p])[p.id],
                       unit_values=unit_stock_values(db, [p]) if show_cost else None)


def product_out(p: Product, default_reorder: int, show_cost: bool,
                opening_open: bool = False,
                unit_values: dict[int, Decimal] | None = None) -> ProductOut:
    level = p.reorder_level if p.reorder_level is not None else default_reorder
    value, each = _valuation(p, unit_values or {}) if show_cost else (None, None)
    return ProductOut(
        id=p.id, sku=p.sku, barcode=p.barcode, name=p.name,
        category_id=p.category_id, category_name=p.category.name if p.category else None,
        brand_id=p.brand_id, brand_name=p.brand.name if p.brand else None,
        model=p.model, specifications=p.specifications, description=p.description,
        tracking_type=p.tracking_type, condition=p.condition,
        selling_price=p.selling_price,
        avg_cost=p.avg_cost if show_cost else None,
        stock_value=value, cost_each=each,
        qty_on_hand=p.qty_on_hand, reorder_level=p.reorder_level,
        effective_reorder_level=level,
        is_low_stock=p.is_active and p.qty_on_hand <= level,
        is_active=p.is_active, opening_stock_open=opening_open,
        created_at=p.created_at, updated_at=p.updated_at,
    )


def _check_category(db: Session, category_id: int | None) -> None:
    if category_id is None:
        return
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(UNPROCESSABLE, "That category does not exist.")
    if not category.is_active:
        raise HTTPException(UNPROCESSABLE,
                            f"The category “{category.name}” is turned off.")


def _check_brand(db: Session, brand_id: int | None) -> None:
    if brand_id is None:
        return
    brand = db.get(Brand, brand_id)
    if brand is None:
        raise HTTPException(UNPROCESSABLE, "That brand does not exist.")
    if not brand.is_active:
        raise HTTPException(UNPROCESSABLE,
                            f"The brand “{brand.name}” is turned off.")


def _check_name_free(db: Session, name: str | None, exclude_id: int | None = None) -> None:
    """One product per name (ignoring capitals and extra spaces), so stock isn't split across
    two copies of the same item. Different variants should say so in the name
    (e.g. "iPhone 13 128GB used")."""
    if not name:
        return
    q = select(Product).where(
        func.lower(func.regexp_replace(func.trim(Product.name), r"\s+", " ", "g"))
        == " ".join(name.split()).lower())
    if exclude_id is not None:
        q = q.where(Product.id != exclude_id)
    existing = db.scalar(q.limit(1))
    if existing is not None:
        hint = (" It is turned off: tick “Show turned-off products”, edit it and turn it back on."
                if not existing.is_active else
                " Use that one, or add a detail to this name (e.g. colour, storage, used).")
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"A product called “{existing.name}” already exists "
                            f"(SKU {existing.sku}).{hint}")


def _check_codes_free(db: Session, sku: str | None, barcode: str | None,
                      exclude_id: int | None = None) -> None:
    for column, value, label in ((Product.sku, sku, "SKU"), (Product.barcode, barcode, "barcode")):
        if value is None:
            continue
        q = select(Product.name).where(column == value)
        if exclude_id is not None:
            q = q.where(Product.id != exclude_id)
        owner_name = db.scalar(q)
        if owner_name:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"The {label} {value} is already used by “{owner_name}”.")


def _condition_for(tracking_type: str, condition: str | None) -> str | None:
    if tracking_type == TrackingType.SERIALIZED:
        if condition is not None:
            raise HTTPException(
                UNPROCESSABLE,
                "Serial/IMEI-tracked products record condition on each unit, not on the product.",
            )
        return None
    return condition or Condition.NEW


def _next_sku(db: Session) -> str:
    while True:
        sku = f"JDT-{db.scalar(select(PRODUCT_SKU_SEQ.next_value())):05d}"
        if db.scalar(select(Product.id).where(Product.sku == sku)) is None:
            return sku


def create_product(db: Session, data: ProductCreate, actor: User,
                   request: Request | None, *, commit: bool = True) -> Product:
    _check_category(db, data.category_id)
    _check_brand(db, data.brand_id)
    _check_name_free(db, data.name)
    _check_codes_free(db, data.sku, data.barcode)
    product = Product(
        **data.model_dump(exclude={"sku", "condition", "opening_quantity", "opening_unit_cost"}),
        sku=data.sku or _next_sku(db),
        condition=_condition_for(data.tracking_type, data.condition),
        qty_on_hand=0,
        is_active=True,
        created_by_id=actor.id,
    )
    db.add(product)
    db.flush()
    audit.record(db, action="product.created", user=actor, entity_type="product",
                 entity_id=product.id,
                 details={"sku": product.sku, "name": product.name,
                          "tracking_type": product.tracking_type,
                          "selling_price": str(product.selling_price)},
                 request=request)
    if data.opening_quantity is not None:
        # Same rules and ledger entry as the separate opening-stock form, in this transaction:
        # the product and its opening stock are saved together or not at all.
        add_opening_stock(db, product, data.opening_quantity, data.opening_unit_cost,
                          note="Entered when the product was added", actor=actor,
                          request=request)
    if not commit:  # part of a bigger transaction (e.g. a spreadsheet import)
        db.flush()
        return product
    db.commit()
    db.refresh(product)
    return product


def update_product(db: Session, product_id: int, data: ProductUpdate, actor: User,
                   request: Request | None) -> Product:
    product = db.get(Product, product_id, with_for_update=True)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    fields = data.model_dump(exclude_unset=True)

    if fields.get("category_id") not in (None, product.category_id):
        _check_category(db, fields["category_id"])
    if fields.get("brand_id") not in (None, product.brand_id):
        _check_brand(db, fields["brand_id"])
    _check_name_free(db, fields.get("name"), exclude_id=product.id)
    _check_codes_free(db, fields.get("sku"), fields.get("barcode"), exclude_id=product.id)

    tracking = fields.get("tracking_type", product.tracking_type)
    if tracking != product.tracking_type and has_movements(db, product.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This product already has stock history, so it cannot switch between "
            "serial/IMEI tracking and quantity tracking. Create a new product instead.",
        )
    if "condition" in fields or tracking != product.tracking_type:
        condition = fields.get("condition", product.condition)
        if tracking == TrackingType.SERIALIZED and "condition" not in fields:
            condition = None  # switching to serialized: condition moves to the units
        fields["condition"] = _condition_for(tracking, condition)

    changes: dict = {}
    for field, new in fields.items():
        _change(changes, product, field, new)
    if not changes:
        db.rollback()
        return product
    audit.record(db, action="product.updated", user=actor, entity_type="product",
                 entity_id=product.id, details=changes, request=request)
    db.commit()
    db.refresh(product)
    return product


def get_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


_SORTS = {
    "name": func.lower(Product.name),
    "sku": Product.sku,
    "price": Product.selling_price,
    "qty": Product.qty_on_hand,
    "updated": Product.updated_at,
}


def list_products(
    db: Session,
    *,
    q: str | None = None,
    category_id: int | None = None,
    brand_id: int | None = None,
    tracking_type: str | None = None,
    stock: StockFilter | None = None,
    include_inactive: bool = False,
    sort: ProductSort = "name",
    descending: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Product], int, int]:
    """Returns (products on this page, total matching, the shop's default reorder level)."""
    default_reorder = default_reorder_level(db)
    conditions = []
    if not include_inactive:
        conditions.append(Product.is_active.is_(True))
    if q and q.strip():
        pattern = like_pattern(q.strip())
        # Typing a phone's serial or IMEI finds its product.
        unit_match = select(ProductUnit.product_id).where(or_(
            *(col.ilike(pattern, escape="\\") for col in
              (ProductUnit.serial_number, ProductUnit.imei1, ProductUnit.imei2))
        ))
        conditions.append(or_(Product.id.in_(unit_match),
                              *(col.ilike(pattern, escape="\\") for col in
                                (Product.name, Product.sku, Product.barcode, Product.model))))
    if category_id is not None:
        # A top-level category also matches products in its subcategories.
        children = select(Category.id).where(Category.parent_id == category_id)
        conditions.append(or_(Product.category_id == category_id,
                              Product.category_id.in_(children)))
    if brand_id is not None:
        conditions.append(Product.brand_id == brand_id)
    if tracking_type is not None:
        conditions.append(Product.tracking_type == tracking_type)
    if stock == "out":
        conditions.append(Product.qty_on_hand == 0)
    elif stock == "in":
        conditions.append(Product.qty_on_hand > 0)
    elif stock == "low":
        conditions.append(Product.qty_on_hand <= func.coalesce(Product.reorder_level,
                                                               default_reorder))

    total = db.scalar(select(func.count()).select_from(Product).where(*conditions)) or 0
    order = _SORTS[sort].desc() if descending else _SORTS[sort].asc()
    products = db.scalars(
        select(Product).where(*conditions).order_by(order, Product.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return list(products), total, default_reorder
