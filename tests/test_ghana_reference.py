import pytest

from isbank.reference.ghana import is_valid_digital_address, is_valid_ghana_card, mobile_network


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("GHA-123456789-0", True),
        ("GHA-12345678-0", False),
        ("gha-123456789-0", False),
        ("GHA123456789-0", False),
        ("GHA-123456789-01", False),
    ],
)
def test_ghana_card_shape(value: str, valid: bool):
    assert is_valid_ghana_card(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"),
    [("GA-123-4567", True), ("AK-1234-5678", True), ("GA-12-4567", False), ("ga-123-4567", False)],
)
def test_digital_address_shape(value: str, valid: bool):
    assert is_valid_digital_address(value) is valid


@pytest.mark.parametrize(
    ("value", "network"),
    [
        ("0244123456", "MTN"),
        ("0501234567", "Telecel"),
        ("0561234567", "AirtelTigo"),
        ("0311234567", None),  # landline prefix
        ("024412345", None),  # too short
        ("+233244123456", None),  # international format is not canonical
    ],
)
def test_mobile_network_from_prefix(value: str, network: str | None):
    assert mobile_network(value) == network
