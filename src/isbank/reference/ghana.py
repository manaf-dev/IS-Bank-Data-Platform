"""Canonical Ghanaian identifier formats.

Defined once here so the generator produces them and Silver validates them against the
same rules. Only the *shape* of each identifier is modelled: values are random and
never derived from, or intended to match, real people.
"""

import re

# Ghana Card (National ID): "GHA-" + 9 digits + "-" + 1 check digit.
GHANA_CARD_PATTERN = re.compile(r"^GHA-\d{9}-\d$")

# Ghana Post GPS digital address: 2-letter district code, then 3-4 and 4 digits.
DIGITAL_ADDRESS_PATTERN = re.compile(r"^[A-Z]{2}-\d{3,4}-\d{4}$")

# Mobile numbers in national format: 0 + 2-digit network code + 7 digits.
MOBILE_NUMBER_PATTERN = re.compile(r"^0\d{9}$")

MOBILE_PREFIXES: dict[str, tuple[str, ...]] = {
    "MTN": ("024", "054", "055", "059"),
    "Telecel": ("020", "050"),
    "AirtelTigo": ("026", "056"),
}


def is_valid_ghana_card(value: str) -> bool:
    """Return True if `value` has the canonical Ghana Card shape (no check-digit math)."""
    return bool(GHANA_CARD_PATTERN.match(value))


def is_valid_digital_address(value: str) -> bool:
    """Return True if `value` has the canonical Ghana Post GPS shape."""
    return bool(DIGITAL_ADDRESS_PATTERN.match(value))


def mobile_network(value: str) -> str | None:
    """Return the network a national-format mobile number belongs to, or None if unknown."""
    if not MOBILE_NUMBER_PATTERN.match(value):
        return None
    prefix = value[:3]
    return next(
        (network for network, prefixes in MOBILE_PREFIXES.items() if prefix in prefixes),
        None,
    )
