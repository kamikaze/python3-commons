from datetime import date

from python3_commons.helpers import date_range


def test_date_range():
    expected_dates = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    dates = tuple(date_range(date(2024, 1, 1), date(2024, 1, 5)))

    assert dates == expected_dates
