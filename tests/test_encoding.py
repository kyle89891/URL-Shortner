import pytest

from app.encoding import decode, encode


@pytest.mark.parametrize(
    "number",
    [0, 1, 61, 62, 63, 125, 12345, 999999, 2**31, 2**53],
)
def test_round_trip(number):
    assert decode(encode(number)) == number


def test_encode_zero():
    assert encode(0) == "0"


def test_encode_is_shorter_than_decimal_for_large_numbers():
    number = 1_000_000_000
    assert len(encode(number)) < len(str(number))


def test_decode_rejects_unknown_characters():
    with pytest.raises(ValueError):
        decode("!!!")
