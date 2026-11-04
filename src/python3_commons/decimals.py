# pyright: strict
import warnings
from decimal import ROUND_HALF_UP, Decimal

__all__ = ['round_decimal']


def round_decimal(value: Decimal, decimal_places: int = 2, rounding_mode: str = ROUND_HALF_UP) -> Decimal:
    if isinstance(value, float):
        warnings.warn(
            'round_decimal: float input is returned unchanged today; in 1.0 it is converted to Decimal and rounded',
            DeprecationWarning,
            stacklevel=2,
        )

    try:
        return value.quantize(Decimal(10) ** -decimal_places, rounding=rounding_mode)
    except AttributeError:
        return value
