import hashlib
import re
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.core.money import quantize_money
from src.modules.catalog import models
from src.modules.catalog.normalization import normalize_search_text

TITAN_ENCODING = "cp1256"
DRUGEYE_RECORD_SIZE = 544
BRANCH_RECORD_SIZE = 856
INTERACTION_RECORD_SIZE = 1300
SOURCE_DRUGEYE = "titan_drugeye"
SOURCE_BRANCH = "titan_branch_master"
SOURCE_NEW_DRUG = "titan_new_drug"
SOURCE_DDI = "titan_ddi"


@dataclass(frozen=True)
class SourceRecord:
    source_name: str
    record_id: str
    checksum: str
    updated_on: date | None = None
    source_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class AliasRecord:
    alias: str
    language: str
    alias_type: str
    source_name: str


@dataclass(frozen=True)
class BarcodeRecord:
    barcode: str
    source_name: str
    is_valid_gtin: bool


@dataclass
class CatalogEntry:
    canonical_key: str
    display_name: str
    name_en: str | None
    name_ar: str | None
    active_ingredients: str | None = None
    manufacturer: str | None = None
    reference_price: Decimal | None = None
    units_per_package: int | None = None
    package_size: int | None = None
    package_unit: str | None = None
    dosage_form: str | None = None
    therapeutic_category: str | None = None
    data_quality_flags: list[str] = field(default_factory=list)
    needs_review: bool = False
    sources: list[SourceRecord] = field(default_factory=list)
    aliases: list[AliasRecord] = field(default_factory=list)
    barcodes: list[BarcodeRecord] = field(default_factory=list)

    def add_alias(
        self, value: str | None, language: str, alias_type: str, source_name: str
    ) -> None:
        if not value or not normalize_search_text(value):
            return
        candidate = AliasRecord(value, language, alias_type, source_name)
        if candidate not in self.aliases:
            self.aliases.append(candidate)


@dataclass(frozen=True)
class InteractionEntry:
    record_id: str
    ingredient_a: str
    ingredient_b: str
    details: str
    advice: str | None
    checksum: str


@dataclass
class TitanImportPlan:
    products: list[CatalogEntry]
    interactions: list[InteractionEntry]
    stats: dict[str, int]
    file_checksums: dict[str, str]


def _set_data_quality_flags(product: CatalogEntry) -> None:
    flags = []
    if not product.name_en:
        flags.append("missing_english_name")
    if not product.name_ar:
        flags.append("missing_arabic_name")
    if product.canonical_key.startswith(f"{SOURCE_BRANCH}:"):
        flags.append("supplemental_branch_only")
    if product.canonical_key.startswith(f"{SOURCE_NEW_DRUG}:"):
        flags.append("supplemental_new_drug_only")
    if product.reference_price == 0:
        flags.append("zero_reference_price")
    if product.reference_price is not None and product.reference_price > Decimal("1000000"):
        flags.append("reference_price_above_1000000")
    product.data_quality_flags = flags
    product.needs_review = bool(flags)


def _decode(value: bytes) -> str:
    return " ".join(value.decode(TITAN_ENCODING, errors="replace").replace("\x00", " ").split())


def _checksum(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _language(value: str) -> str:
    if any("\u0600" <= character <= "\u06ff" for character in value):
        return "ar"
    if any("a" <= character.casefold() <= "z" for character in value):
        return "en"
    return "und"


def _money(value: float | str) -> Decimal | None:
    try:
        decimal_value = Decimal(str(value))
    except Exception:
        return None
    if not decimal_value.is_finite() or decimal_value < 0 or decimal_value > Decimal("99999999"):
        return None
    return quantize_money(decimal_value)


def _positive_integer(value: int | str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _excel_serial_date(value: str) -> date | None:
    try:
        serial = int(value)
    except ValueError:
        return None
    if serial < 1 or serial > 100_000:
        return None
    return date(1899, 12, 30) + timedelta(days=serial)


def _valid_gtin(value: str) -> bool:
    if not value.isdigit() or len(value) not in {8, 12, 13, 14}:
        return False
    digits = [int(character) for character in value]
    weighted = sum(
        digit * (3 if (len(digits) - index) % 2 == 0 else 1)
        for index, digit in enumerate(digits[:-1])
    )
    expected = (10 - weighted % 10) % 10
    return expected == digits[-1]


def _read_fixed_records(path: Path, record_size: int) -> tuple[bytes, list[bytes]]:
    data = path.read_bytes()
    if not data or len(data) % record_size:
        raise ValueError(f"{path.name} does not match the expected {record_size}-byte layout")
    return data, [data[index : index + record_size] for index in range(0, len(data), record_size)]


def _parse_drugeye(path: Path) -> tuple[list[CatalogEntry], str]:
    data, records = _read_fixed_records(path, DRUGEYE_RECORD_SIZE)
    products = []
    for index, record in enumerate(records, start=1):
        name_en = _decode(record[0:100]) or None
        name_ar = _decode(record[328:378]) or None
        if not name_en and not name_ar:
            continue
        price = _money(struct.unpack_from("<f", record, 300)[0])
        units = _positive_integer(struct.unpack_from("<I", record, 304)[0])
        package_size = _positive_integer(struct.unpack_from("<H", record, 378)[0])
        entry = CatalogEntry(
            canonical_key=f"{SOURCE_DRUGEYE}:{index}",
            display_name=name_en or name_ar or "",
            name_en=name_en,
            name_ar=name_ar,
            active_ingredients=_decode(record[100:200]) or None,
            manufacturer=_decode(record[250:300]) or None,
            reference_price=price,
            units_per_package=units,
            package_size=package_size,
            package_unit=_decode(record[380:400]) or None,
            dosage_form=_decode(record[400:420]) or None,
            therapeutic_category=_decode(record[439:539]) or None,
            needs_review=not bool(name_en and name_ar),
        )
        entry.sources.append(
            SourceRecord(
                SOURCE_DRUGEYE,
                str(index),
                _checksum(record),
                source_data={"record_size": DRUGEYE_RECORD_SIZE},
            )
        )
        entry.add_alias(name_en, "en", "trade_name", SOURCE_DRUGEYE)
        entry.add_alias(name_ar, "ar", "trade_name", SOURCE_DRUGEYE)
        entry.add_alias(entry.active_ingredients, "en", "active_ingredients", SOURCE_DRUGEYE)
        products.append(entry)
    return products, _checksum(data)


def _parse_branch_master(path: Path) -> tuple[list[CatalogEntry], str]:
    data, records = _read_fixed_records(path, BRANCH_RECORD_SIZE)
    products = []
    for index, record in enumerate(records, start=1):
        primary_name = _decode(record[0:40])
        if not primary_name:
            continue
        secondary_name = _decode(record[40:70])
        primary_language = _language(primary_name)
        secondary_language = _language(secondary_name)
        name_en = primary_name if primary_language == "en" else None
        name_ar = secondary_name if secondary_language == "ar" else None
        if name_ar is None and primary_language == "ar":
            name_ar = primary_name
        if name_en is None and secondary_language == "en":
            name_en = secondary_name
        entry = CatalogEntry(
            canonical_key=f"{SOURCE_BRANCH}:{index}",
            display_name=primary_name,
            name_en=name_en,
            name_ar=name_ar,
            active_ingredients=_decode(record[90:130]) or None,
            manufacturer=_decode(record[70:90]) or None,
            reference_price=_money(struct.unpack_from("<f", record, 224)[0]),
            package_size=_positive_integer(struct.unpack_from("<H", record, 264)[0]),
            therapeutic_category=_decode(record[796:846]) or None,
            needs_review=True,
        )
        entry.sources.append(
            SourceRecord(
                SOURCE_BRANCH,
                str(index),
                _checksum(record),
                source_data={"record_size": BRANCH_RECORD_SIZE},
            )
        )
        entry.add_alias(primary_name, primary_language, "legacy_name", SOURCE_BRANCH)
        if secondary_name and normalize_search_text(secondary_name) != normalize_search_text(
            primary_name
        ):
            entry.add_alias(secondary_name, secondary_language, "legacy_name", SOURCE_BRANCH)
        entry.add_alias(entry.active_ingredients, "en", "active_ingredients", SOURCE_BRANCH)
        products.append(entry)
    return products, _checksum(data)


def _parse_new_drugs(path: Path) -> tuple[list[dict[str, str]], str]:
    data = path.read_bytes()
    content = data.decode(TITAN_ENCODING, errors="replace")
    rows = []
    for block in content.split("<sep>"):
        row = {
            field_name: match.group(1).strip()
            for field_name in ("id", "date", "name", "price", "units", "dariba", "barcode")
            if (match := re.search(rf"<{field_name}>(.*?)<\\{field_name}>", block, flags=re.DOTALL))
        }
        if row.get("id") and row.get("name"):
            row["checksum"] = _checksum(block.encode(TITAN_ENCODING, errors="replace"))
            rows.append(row)
    return rows, _checksum(data)


def _parse_interactions(path: Path) -> tuple[list[InteractionEntry], str]:
    data, records = _read_fixed_records(path, INTERACTION_RECORD_SIZE)
    interactions = []
    for index, record in enumerate(records, start=1):
        ingredient_a = _decode(record[0:50])
        ingredient_b = _decode(record[50:100])
        details = _decode(record[100:1300])
        if not ingredient_a or not ingredient_b or not details:
            raise ValueError(f"Interaction record {index} is incomplete")
        advice_match = re.search(r"(?:^|\s)Advice:\s*(.+)$", details, flags=re.IGNORECASE)
        interactions.append(
            InteractionEntry(
                record_id=str(index),
                ingredient_a=ingredient_a,
                ingredient_b=ingredient_b,
                details=details,
                advice=advice_match.group(1).strip() if advice_match else None,
                checksum=_checksum(record),
            )
        )
    return interactions, _checksum(data)


def _build_alias_lookup(products: Iterable[CatalogEntry]) -> dict[str, set[str]]:
    lookup: dict[str, set[str]] = defaultdict(set)
    for product in products:
        for alias in product.aliases:
            normalized = normalize_search_text(alias.alias)
            if normalized:
                lookup[normalized].add(product.canonical_key)
    return lookup


def _match_entry(entry: CatalogEntry, lookup: dict[str, set[str]]) -> str | None:
    primary_aliases = [
        alias for alias in entry.aliases if alias.alias_type in {"trade_name", "legacy_name"}
    ]
    candidate_sets = [
        lookup[normalized]
        for alias in primary_aliases
        if (normalized := normalize_search_text(alias.alias)) and normalized in lookup
    ]
    nonempty = [candidates for candidates in candidate_sets if candidates]
    if not nonempty:
        return None
    intersection = set.intersection(*nonempty)
    if len(intersection) == 1:
        return next(iter(intersection))
    union = set.union(*nonempty)
    return next(iter(union)) if len(union) == 1 else None


def build_titan_import_plan(
    *,
    drugeye_path: Path,
    branch_master_path: Path,
    new_drug_path: Path,
    interactions_path: Path,
) -> TitanImportPlan:
    products, drugeye_checksum = _parse_drugeye(drugeye_path)
    products_by_key = {product.canonical_key: product for product in products}
    lookup = _build_alias_lookup(products)

    branch_products, branch_checksum = _parse_branch_master(branch_master_path)
    branch_matched = 0
    branch_supplemental = 0
    for branch_product in branch_products:
        matched_key = _match_entry(branch_product, lookup)
        if matched_key is None:
            products.append(branch_product)
            products_by_key[branch_product.canonical_key] = branch_product
            for alias in branch_product.aliases:
                lookup[normalize_search_text(alias.alias)].add(branch_product.canonical_key)
            branch_supplemental += 1
            continue
        target = products_by_key[matched_key]
        target.sources.extend(branch_product.sources)
        for alias in branch_product.aliases:
            target.add_alias(alias.alias, alias.language, alias.alias_type, alias.source_name)
        branch_matched += 1

    new_drugs, new_drug_checksum = _parse_new_drugs(new_drug_path)
    new_drug_matched = 0
    new_drug_supplemental = 0
    barcodes_added = 0
    for row in new_drugs:
        name = row["name"]
        normalized = normalize_search_text(name)
        candidates = lookup.get(normalized, set())
        if len(candidates) == 1:
            target = products_by_key[next(iter(candidates))]
            new_drug_matched += 1
        else:
            key = f"{SOURCE_NEW_DRUG}:{row['id']}"
            language = _language(name)
            target = CatalogEntry(
                canonical_key=key,
                display_name=name,
                name_en=name if language == "en" else None,
                name_ar=name if language == "ar" else None,
                reference_price=_money(row.get("price", "")),
                units_per_package=_positive_integer(row.get("units", "")),
                needs_review=True,
            )
            target.add_alias(name, language, "legacy_name", SOURCE_NEW_DRUG)
            products.append(target)
            products_by_key[key] = target
            lookup[normalized].add(key)
            new_drug_supplemental += 1

        updated_on = _excel_serial_date(row.get("date", ""))
        target.sources.append(
            SourceRecord(
                SOURCE_NEW_DRUG,
                row["id"],
                row["checksum"],
                updated_on=updated_on,
                source_data={
                    "price": row.get("price"),
                    "units": row.get("units"),
                    "tax": row.get("dariba"),
                    "barcode": row.get("barcode"),
                },
            )
        )
        target.add_alias(name, _language(name), "legacy_name", SOURCE_NEW_DRUG)
        if (price := _money(row.get("price", ""))) is not None:
            target.reference_price = price
        if (units := _positive_integer(row.get("units", ""))) is not None:
            target.units_per_package = units
        barcode = row.get("barcode", "").strip()
        if barcode and barcode != "0":
            candidate = BarcodeRecord(barcode, SOURCE_NEW_DRUG, _valid_gtin(barcode))
            if candidate not in target.barcodes:
                target.barcodes.append(candidate)
                barcodes_added += 1

    for product in products:
        _set_data_quality_flags(product)

    interactions, interaction_checksum = _parse_interactions(interactions_path)
    flag_counts: dict[str, int] = defaultdict(int)
    for product in products:
        for flag in product.data_quality_flags:
            flag_counts[flag] += 1
    stats = {
        "drugeye_products": len(products_by_key) - branch_supplemental - new_drug_supplemental,
        "branch_products": len(branch_products),
        "branch_matched": branch_matched,
        "branch_supplemental": branch_supplemental,
        "new_drug_records": len(new_drugs),
        "new_drug_matched": new_drug_matched,
        "new_drug_supplemental": new_drug_supplemental,
        "catalog_products_planned": len(products),
        "aliases_planned": sum(len(product.aliases) for product in products),
        "barcodes_planned": sum(len(product.barcodes) for product in products),
        "barcodes_added": barcodes_added,
        "interactions_planned": len(interactions),
        "needs_review": sum(product.needs_review for product in products),
        **{f"quality_{flag}": count for flag, count in sorted(flag_counts.items())},
    }
    return TitanImportPlan(
        products=products,
        interactions=interactions,
        stats=stats,
        file_checksums={
            SOURCE_DRUGEYE: drugeye_checksum,
            SOURCE_BRANCH: branch_checksum,
            SOURCE_NEW_DRUG: new_drug_checksum,
            SOURCE_DDI: interaction_checksum,
        },
    )


def _chunks(rows: list[dict[str, Any]], size: int = 1000) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _dedupe_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    deduplicated = {tuple(row[field_name] for field_name in key_fields): row for row in rows}
    return list(deduplicated.values())


def apply_titan_import(db: Session, plan: TitanImportPlan) -> dict[str, int]:
    product_rows = [
        {
            "canonical_key": product.canonical_key,
            "display_name": product.display_name,
            "name_en": product.name_en,
            "name_ar": product.name_ar,
            "active_ingredients": product.active_ingredients,
            "manufacturer": product.manufacturer,
            "reference_price": product.reference_price,
            "units_per_package": product.units_per_package,
            "package_size": product.package_size,
            "package_unit": product.package_unit,
            "dosage_form": product.dosage_form,
            "therapeutic_category": product.therapeutic_category,
            "data_quality_flags": product.data_quality_flags,
            "is_active": True,
            "needs_review": product.needs_review,
        }
        for product in plan.products
    ]
    try:
        for batch in _chunks(product_rows):
            statement = insert(models.CatalogProduct).values(batch)
            update_columns = {
                column: getattr(statement.excluded, column)
                for column in product_rows[0]
                if column != "canonical_key"
            }
            update_columns["updated_at"] = func.now()
            db.execute(
                statement.on_conflict_do_update(
                    index_elements=[models.CatalogProduct.canonical_key],
                    set_=update_columns,
                )
            )
        key_to_id = dict(
            db.query(models.CatalogProduct.canonical_key, models.CatalogProduct.id)
            .filter(
                models.CatalogProduct.canonical_key.in_(
                    [product.canonical_key for product in plan.products]
                )
            )
            .all()
        )

        source_rows = []
        alias_rows = []
        barcode_rows = []
        for product in plan.products:
            product_id = key_to_id[product.canonical_key]
            for source in product.sources:
                source_rows.append(
                    {
                        "catalog_product_id": product_id,
                        "source_name": source.source_name,
                        "source_record_id": source.record_id,
                        "source_checksum": source.checksum,
                        "source_updated_on": source.updated_on,
                        "source_data": source.source_data,
                    }
                )
            for alias in product.aliases:
                alias_rows.append(
                    {
                        "catalog_product_id": product_id,
                        "alias": alias.alias,
                        "normalized_alias": normalize_search_text(alias.alias),
                        "language": alias.language,
                        "alias_type": alias.alias_type,
                        "source_name": alias.source_name,
                    }
                )
            for barcode in product.barcodes:
                barcode_rows.append(
                    {
                        "catalog_product_id": product_id,
                        "barcode": barcode.barcode,
                        "source_name": barcode.source_name,
                        "is_valid_gtin": barcode.is_valid_gtin,
                    }
                )

        source_rows = _dedupe_rows(source_rows, ("source_name", "source_record_id"))
        alias_rows = _dedupe_rows(
            alias_rows,
            ("catalog_product_id", "normalized_alias", "alias_type", "source_name"),
        )
        barcode_rows = _dedupe_rows(barcode_rows, ("catalog_product_id", "barcode"))

        for batch in _chunks(source_rows):
            statement = insert(models.CatalogProductSource).values(batch)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_catalog_sources_name_record",
                    set_={
                        "catalog_product_id": statement.excluded.catalog_product_id,
                        "source_checksum": statement.excluded.source_checksum,
                        "source_updated_on": statement.excluded.source_updated_on,
                        "source_data": statement.excluded.source_data,
                        "imported_at": func.now(),
                    },
                )
            )
        for batch in _chunks(alias_rows):
            statement = insert(models.CatalogProductAlias).values(batch)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_catalog_aliases_product_value_type_source",
                    set_={
                        "alias": statement.excluded.alias,
                        "language": statement.excluded.language,
                    },
                )
            )
        for batch in _chunks(barcode_rows):
            statement = insert(models.CatalogProductBarcode).values(batch)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_catalog_barcodes_product_barcode",
                    set_={
                        "source_name": statement.excluded.source_name,
                        "is_valid_gtin": statement.excluded.is_valid_gtin,
                    },
                )
            )

        interaction_rows = [
            {
                "source_name": SOURCE_DDI,
                "source_record_id": interaction.record_id,
                "ingredient_a": interaction.ingredient_a,
                "ingredient_b": interaction.ingredient_b,
                "normalized_ingredient_a": normalize_search_text(interaction.ingredient_a),
                "normalized_ingredient_b": normalize_search_text(interaction.ingredient_b),
                "details": interaction.details,
                "advice": interaction.advice,
                "clinical_status": "legacy_unverified",
                "source_checksum": interaction.checksum,
            }
            for interaction in plan.interactions
        ]
        for batch in _chunks(interaction_rows):
            statement = insert(models.CatalogDrugInteraction).values(batch)
            db.execute(
                statement.on_conflict_do_update(
                    constraint="uq_catalog_interactions_source_record",
                    set_={
                        "ingredient_a": statement.excluded.ingredient_a,
                        "ingredient_b": statement.excluded.ingredient_b,
                        "normalized_ingredient_a": statement.excluded.normalized_ingredient_a,
                        "normalized_ingredient_b": statement.excluded.normalized_ingredient_b,
                        "details": statement.excluded.details,
                        "advice": statement.excluded.advice,
                        "source_checksum": statement.excluded.source_checksum,
                        "imported_at": func.now(),
                    },
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise

    imported_keys = [product.canonical_key for product in plan.products]
    return {
        "catalog_products": db.query(models.CatalogProduct)
        .filter(models.CatalogProduct.canonical_key.in_(imported_keys))
        .count(),
        "catalog_sources": db.query(models.CatalogProductSource)
        .filter(models.CatalogProductSource.source_name.in_(plan.file_checksums))
        .count(),
        "catalog_aliases": db.query(models.CatalogProductAlias)
        .join(models.CatalogProduct)
        .filter(models.CatalogProduct.canonical_key.in_(imported_keys))
        .count(),
        "catalog_barcodes": db.query(models.CatalogProductBarcode)
        .join(models.CatalogProduct)
        .filter(models.CatalogProduct.canonical_key.in_(imported_keys))
        .count(),
        "catalog_interactions": db.query(models.CatalogDrugInteraction)
        .filter(models.CatalogDrugInteraction.source_name == SOURCE_DDI)
        .count(),
    }
