from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CatalogProductResponse(BaseModel):
    id: int
    display_name: str
    name_en: str | None
    name_ar: str | None
    active_ingredients: str | None
    manufacturer: str | None
    reference_price: Decimal | None
    units_per_package: int | None
    package_size: int | None
    package_unit: str | None
    dosage_form: str | None
    therapeutic_category: str | None
    barcode: str | None = None
    data_quality_flags: list[str]
    needs_review: bool

    model_config = ConfigDict(from_attributes=True)
