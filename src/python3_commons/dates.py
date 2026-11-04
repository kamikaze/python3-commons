from datetime import date, datetime, timedelta, tzinfo

__all__ = ['date_from_string', 'date_range', 'datetime_from_string']

_DATETIME_FORMAT = '%d.%m.%Y %H:%M:%S'


def date_from_string(
    string: str,
    fmt: str = '%d.%m.%Y',
    *,
    tz: tzinfo | None = None,  # noqa: ARG001  # accepted for symmetry, public signature
) -> date:
    """Parse `fmt` or an ISO 8601 date.

    `tz` is accepted for symmetry with `datetime_from_string`; a `date` carries no timezone, so it does not change
    the result.
    """
    try:
        return datetime.strptime(string, fmt).date()  # noqa: DTZ007  # a date has no timezone
    except ValueError:
        pass

    try:
        return date.fromisoformat(string)
    except ValueError as exc:
        msg = f'{string!r} is neither {fmt!r} nor an ISO 8601 date (YYYY-MM-DD)'
        raise ValueError(msg) from exc


def datetime_from_string(string: str, *, tz: tzinfo | None = None) -> datetime:
    """Parse '%d.%m.%Y %H:%M:%S' or an ISO 8601 datetime; `tz` is applied to naive results only."""
    try:
        result = datetime.strptime(string, _DATETIME_FORMAT)  # noqa: DTZ007  # `tz` is applied below
    except ValueError:
        try:
            result = datetime.fromisoformat(string)
        except ValueError as exc:
            msg = f'{string!r} is neither {_DATETIME_FORMAT!r} nor an ISO 8601 datetime'
            raise ValueError(msg) from exc

    if tz is not None and result.tzinfo is None:
        return result.replace(tzinfo=tz)

    return result


def date_range(start_date, end_date):  # noqa: ANN001, ANN201  # pyright: ignore[reportMissingParameterType]  # untyped public API: date or datetime in, same out
    for n in range(int((end_date - start_date).days + 1)):
        yield start_date + timedelta(days=n)
