import re

_TAGS = re.compile(r"<[^>]*>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]+")


def clean_text(value: str, max_length: int) -> str:
    """Strip HTML tags and control characters, collapse spaces, cap length."""
    value = _TAGS.sub("", value)
    value = _CONTROL.sub("", value)
    value = _SPACES.sub(" ", value).strip()
    return value[:max_length]
