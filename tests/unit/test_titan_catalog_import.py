import struct
from pathlib import Path

import pytest

from src.modules.catalog.titan_import import (
    BRANCH_RECORD_SIZE,
    DRUGEYE_RECORD_SIZE,
    INTERACTION_RECORD_SIZE,
    SOURCE_BRANCH,
    SOURCE_DRUGEYE,
    SOURCE_NEW_DRUG,
    build_titan_import_plan,
)


def _write_text(buffer: bytearray, start: int, end: int, value: str) -> None:
    encoded = value.encode("cp1256")
    assert len(encoded) <= end - start
    buffer[start:end] = encoded.ljust(end - start, b" ")


def _drugeye_record(name_en: str, name_ar: str) -> bytes:
    record = bytearray(DRUGEYE_RECORD_SIZE)
    _write_text(record, 0, 100, name_en)
    _write_text(record, 100, 200, "PARACETAMOL")
    _write_text(record, 250, 300, "SYNTHETIC PHARMA")
    struct.pack_into("<f", record, 300, 50.0)
    struct.pack_into("<I", record, 304, 2)
    _write_text(record, 328, 378, name_ar)
    struct.pack_into("<H", record, 378, 20)
    _write_text(record, 380, 400, "TABLETS")
    _write_text(record, 400, 420, "TABLET")
    _write_text(record, 439, 539, "ANALGESIC")
    return bytes(record)


def _branch_record(name: str, name_ar: str = "") -> bytes:
    record = bytearray(BRANCH_RECORD_SIZE)
    _write_text(record, 0, 40, name)
    _write_text(record, 40, 70, name_ar)
    _write_text(record, 70, 90, "SYNTHETIC")
    _write_text(record, 90, 130, "PARACETAMOL")
    struct.pack_into("<f", record, 224, 55.0)
    struct.pack_into("<H", record, 264, 20)
    _write_text(record, 796, 846, "ANALGESIC")
    return bytes(record)


def _interaction_record() -> bytes:
    record = bytearray(INTERACTION_RECORD_SIZE)
    _write_text(record, 0, 50, "PARACETAMOL")
    _write_text(record, 50, 100, "SYNTHETIC INGREDIENT")
    _write_text(record, 100, 1300, "Synthetic interaction. Advice: Monitor")
    return bytes(record)


def _write_fixture_files(root: Path) -> dict[str, Path]:
    paths = {
        "drugeye": root / "drugeye.phy",
        "branch": root / "tar.phy",
        "new_drug": root / "New.drug.txt",
        "interactions": root / "DDI.Phy",
    }
    paths["drugeye"].write_bytes(_drugeye_record("PANADOL 500 MG 20 TAB", "بانادول 500 مجم"))
    paths["branch"].write_bytes(
        _branch_record("PANADOL 500 MG 20 TAB", "بانادول 500 مجم")
        + _branch_record("SYNTHETIC LOCAL ITEM")
    )
    paths["new_drug"].write_bytes(
        (
            "<id>1<\\id><date>45000<\\date><name>PANADOL 500 MG 20 TAB<\\name>"
            "<price>60<\\price><units>2<\\units><dariba>0<\\dariba>"
            "<barcode>4006381333931<\\barcode><sep>"
        ).encode("cp1256")
    )
    paths["interactions"].write_bytes(_interaction_record())
    return paths


def test_titan_plan_merges_exact_names_and_preserves_supplemental_products(
    tmp_path: Path,
) -> None:
    paths = _write_fixture_files(tmp_path)

    plan = build_titan_import_plan(
        drugeye_path=paths["drugeye"],
        branch_master_path=paths["branch"],
        new_drug_path=paths["new_drug"],
        interactions_path=paths["interactions"],
    )

    assert plan.stats["catalog_products_planned"] == 2
    assert plan.stats["branch_matched"] == 1
    assert plan.stats["branch_supplemental"] == 1
    assert plan.stats["new_drug_matched"] == 1
    assert plan.stats["barcodes_planned"] == 1
    assert plan.stats["interactions_planned"] == 1

    panadol = next(
        product for product in plan.products if product.canonical_key == f"{SOURCE_DRUGEYE}:1"
    )
    assert {source.source_name for source in panadol.sources} == {
        SOURCE_DRUGEYE,
        SOURCE_BRANCH,
        SOURCE_NEW_DRUG,
    }
    assert panadol.reference_price is not None
    assert str(panadol.reference_price) == "60.00"
    assert panadol.barcodes[0].is_valid_gtin is True
    assert panadol.data_quality_flags == []

    supplemental = next(
        product for product in plan.products if product.canonical_key == f"{SOURCE_BRANCH}:2"
    )
    assert supplemental.data_quality_flags == [
        "missing_arabic_name",
        "supplemental_branch_only",
    ]


def test_titan_plan_rejects_an_invalid_fixed_record_layout(tmp_path: Path) -> None:
    paths = _write_fixture_files(tmp_path)
    paths["drugeye"].write_bytes(b"invalid")

    with pytest.raises(ValueError, match="544-byte layout"):
        build_titan_import_plan(
            drugeye_path=paths["drugeye"],
            branch_master_path=paths["branch"],
            new_drug_path=paths["new_drug"],
            interactions_path=paths["interactions"],
        )
