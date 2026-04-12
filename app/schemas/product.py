from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel


class ProductCatalogItem(BaseModel):
    product_name: str
    inventory_item_id: UUID | None = None
    sku: str | None = None
    total_sales_revenue: int = 0
    total_purchase_cost: int = 0
    gross_profit: int = 0
    margin_pct: float | None = None
    sales_invoice_count: int = 0
    purchase_invoice_count: int = 0
    client_count: int = 0
    supplier_count: int = 0


class ProductCatalogResponse(BaseModel):
    items: list[ProductCatalogItem]
    total_revenue: int = 0
    total_cost: int = 0
    total_profit: int = 0


class ProductClientRow(BaseModel):
    entity_id: UUID
    name: str
    revenue: int = 0
    invoice_count: int = 0


class ProductSupplierRow(BaseModel):
    entity_id: UUID
    name: str
    cost: int = 0
    invoice_count: int = 0


class ProductInvoiceRow(BaseModel):
    invoice_id: UUID
    number: str
    kind: str
    issue_date: date
    entity_name: str | None = None
    quantity: float = 0
    unit_price: int = 0
    line_total: int = 0


class ProductMonthlyRow(BaseModel):
    month: str
    revenue: int = 0
    cost: int = 0


class ProductDetailResponse(BaseModel):
    product_name: str
    sku: str | None = None
    inventory_item_id: UUID | None = None
    total_revenue: int = 0
    total_cost: int = 0
    gross_profit: int = 0
    margin_pct: float | None = None
    clients: list[ProductClientRow] = []
    suppliers: list[ProductSupplierRow] = []
    invoices: list[ProductInvoiceRow] = []
    monthly_series: list[ProductMonthlyRow] = []


class EntityProductRow(BaseModel):
    product_name: str
    revenue: int = 0
    cost: int = 0
    invoice_count: int = 0


class EntityRelationshipRow(BaseModel):
    entity_id: UUID
    entity_name: str
    entity_type: str
    products: list[EntityProductRow] = []
    total_revenue: int = 0
    total_cost: int = 0
    profit: int = 0


class ProductSummaryRow(BaseModel):
    product_name: str
    total_revenue: int = 0
    total_cost: int = 0
    entity_count: int = 0


class EntityMatrixResponse(BaseModel):
    relationships: list[EntityRelationshipRow]
    product_summary: list[ProductSummaryRow]


class ProfitByProduct(BaseModel):
    product_name: str
    revenue: int = 0
    cost: int = 0
    profit: int = 0
    margin_pct: float | None = None
    top_client: str | None = None


class ProfitByClientProduct(BaseModel):
    client_name: str
    product_name: str
    revenue: int = 0
    cost: int = 0
    profit: int = 0


class ProfitabilityResponse(BaseModel):
    by_product: list[ProfitByProduct]
    by_client_product: list[ProfitByClientProduct]
    total_revenue: int = 0
    total_cost: int = 0
    total_profit: int = 0
    avg_margin: float | None = None
