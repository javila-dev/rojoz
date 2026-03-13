import re
import unicodedata


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_document_number(value: str) -> str:
    """Keep only alphanumeric chars for document identifiers."""
    return re.sub(r"[^A-Za-z0-9]", "", (value or "").strip())


def normalize_person_name(value: str) -> str:
    """
    Keep letters and spaces only.
    Removes digits and punctuation, collapses repeated spaces.
    """
    raw = _strip_accents((value or "").strip())
    only_letters = re.sub(r"[^A-Za-z\s]", " ", raw)
    return re.sub(r"\s+", " ", only_letters).strip()


def normalize_phone(value: str) -> str:
    """Keep only digits."""
    return re.sub(r"\D", "", (value or "").strip())
