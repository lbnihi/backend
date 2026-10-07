import re

KSA_PHONE_RE = re.compile(r"^05[0-9]{8}$")


def is_valid_ksa_phone(phone: str) -> bool:
    return bool(KSA_PHONE_RE.match(phone.strip()))


def _national_digits(phone: str) -> str:
    """'0551234567' / '+966551234567' / '966551234567' -> '551234567'."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("966"):
        digits = digits[3:]
    return digits.lstrip("0")


def to_e164(phone: str, with_plus: bool) -> str:
    """Facebook hashes 966XXXXXXXXX (no +); TikTok and Snapchat hash +966XXXXXXXXX."""
    national = _national_digits(phone)
    return f"{'+' if with_plus else ''}966{national}"


def normalize_phone_fb(phone: str) -> str:
    return to_e164(phone, with_plus=False)


def normalize_phone_tiktok(phone: str) -> str:
    return to_e164(phone, with_plus=True)


def normalize_phone_snap(phone: str) -> str:
    return to_e164(phone, with_plus=True)


def mask_phone(phone: str) -> str:
    """Log-safe form: 0551234567 -> 05XXX4567."""
    if len(phone) >= 10:
        return f"{phone[:2]}XXX{phone[-4:]}"
    return "XXXXX"
