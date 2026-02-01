from datetime import date, datetime
from decimal import Decimal

from python3_commons.helpers import (
    SingletonMeta,
    date_from_string,
    date_range,
    datetime_from_string,
    parse_string_list,
    round_decimal,
    to_snake_case,
)


def test_date_range() -> None:
    expected_dates = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    dates = tuple(date_range(date(2024, 1, 1), date(2024, 1, 5)))

    assert dates == expected_dates


def test_singleton_meta():
    class S(metaclass=SingletonMeta):
        pass

    s1 = S()
    s2 = S()
    assert s1 is s2


def test_date_from_string():
    assert date_from_string('01.01.2024') == date(2024, 1, 1)
    assert date_from_string('2024-01-01') == date(2024, 1, 1)


def test_datetime_from_string():
    assert datetime_from_string('01.01.2024 12:00:00') == datetime(2024, 1, 1, 12, 0, 0)
    assert datetime_from_string('2024-01-01T12:00:00') == datetime(2024, 1, 1, 12, 0, 0)


def test_round_decimal():
    assert round_decimal(Decimal('1.234')) == Decimal('1.23')
    assert round_decimal(Decimal('1.235')) == Decimal('1.24')
    assert round_decimal(1.234) == 1.234  # Should return as is if not Decimal


def test_to_snake_case():
    assert to_snake_case('Foo Bar') == 'foo_bar'
    assert to_snake_case('  Foo  Bar  ') == 'foo_bar'
    assert to_snake_case('Foo-Bar!') == 'foobar'


def test_parse_string_list():
    assert parse_string_list('a, b, c') == ('a', 'b', 'c')
    assert parse_string_list(['a', 'b']) == ['a', 'b']
