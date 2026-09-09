import re
import unicodedata
from dataclasses import dataclass

ARABIC_MARKS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
ARABIC_FOLD = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ـ": "",
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)
LATIN_KEYS_TO_ARABIC = {
    "`": "ذ",
    "q": "ض",
    "w": "ص",
    "e": "ث",
    "r": "ق",
    "t": "ف",
    "y": "غ",
    "u": "ع",
    "i": "ه",
    "o": "خ",
    "p": "ح",
    "[": "ج",
    "]": "د",
    "a": "ش",
    "s": "س",
    "d": "ي",
    "f": "ب",
    "g": "ل",
    "h": "ا",
    "j": "ت",
    "k": "ن",
    "l": "م",
    ";": "ك",
    "'": "ط",
    "z": "ئ",
    "x": "ء",
    "c": "ؤ",
    "v": "ر",
    "b": "لا",
    "n": "ى",
    "m": "ة",
    ",": "و",
    ".": "ز",
    "/": "ظ",
}


@dataclass(frozen=True)
class SearchVariants:
    original: str
    arabic_keyboard: str | None


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(ARABIC_FOLD)
    normalized = ARABIC_MARKS.sub("", normalized)
    searchable = "".join(character if character.isalnum() else " " for character in normalized)
    return " ".join(searchable.split())


def latin_keys_as_arabic(value: str) -> str | None:
    folded = unicodedata.normalize("NFKC", value).casefold()
    if not any("a" <= character <= "z" for character in folded):
        return None
    converted = "".join(LATIN_KEYS_TO_ARABIC.get(character, character) for character in folded)
    normalized = normalize_search_text(converted)
    if not normalized or not any("\u0600" <= character <= "\u06ff" for character in normalized):
        return None
    return normalized


def build_search_variants(value: str) -> SearchVariants:
    original = normalize_search_text(value)
    keyboard_variant = latin_keys_as_arabic(value)
    if keyboard_variant == original:
        keyboard_variant = None
    return SearchVariants(original=original, arabic_keyboard=keyboard_variant)
