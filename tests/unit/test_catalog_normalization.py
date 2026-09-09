from src.modules.catalog.normalization import (
    build_search_variants,
    latin_keys_as_arabic,
    normalize_search_text,
)


def test_normalization_folds_safe_arabic_variants_and_digits() -> None:
    assert normalize_search_text("  إِبْرَةـ ١٠ مِل  ") == "ابرة 10 مل"


def test_latin_keyboard_input_is_interpreted_as_arabic_one_way() -> None:
    variants = build_search_variants("fhkh],g")

    assert variants.original == "fhkh g"
    assert variants.arabic_keyboard == "بانادول"


def test_arabic_input_is_not_interpreted_as_latin_keyboard_input() -> None:
    variants = build_search_variants("بانادول")

    assert variants.original == "بانادول"
    assert variants.arabic_keyboard is None
    assert latin_keys_as_arabic("بانادول") is None


def test_english_query_keeps_original_and_adds_lower_priority_variant() -> None:
    variants = build_search_variants("Panadol")

    assert variants.original == "panadol"
    assert variants.arabic_keyboard is not None
