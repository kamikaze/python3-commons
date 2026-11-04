# pyright: strict
from enum import IntEnum

__all__ = ['ExtendedType']


class ExtendedType(IntEnum):
    DECIMAL = 1
    DATETIME = 2
    DATE = 3
    DATACLASS = 4
