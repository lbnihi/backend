import hashlib


def sha256_hash(value: str) -> str:
    """SHA256 of the trimmed, lowercased value (CAPI normalization rule for every platform)."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def normalize_city(city: str) -> str:
    """Facebook `ct`: lowercase with no spaces."""
    return "".join(city.lower().split())
