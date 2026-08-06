"""Email normalisation helpers."""


def normalize_email(email: str) -> str:
    """Return a canonical, lower-cased, stripped email address."""
    return email.strip().lower()
