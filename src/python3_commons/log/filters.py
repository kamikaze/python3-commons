# pyright: strict
import logging
from typing import Final

__all__ = ('MaxLevelFilter', 'filter_maker')


def _to_level(level: int | str) -> int:
    if isinstance(level, int) and not isinstance(level, bool):
        return level

    if isinstance(level, str):
        try:
            return logging.getLevelNamesMapping()[level.strip().upper()]
        except KeyError:
            pass

    msg = f'Unknown log level: {level!r}'

    raise ValueError(msg)


class MaxLevelFilter(logging.Filter):
    """Pass records whose level is at or below ``level`` (a name in any case, or an int)."""

    def __init__(self, level: int | str, name: str = '') -> None:
        super().__init__(name)

        self.level: int = _to_level(level)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level and bool(super().filter(record))


filter_maker: Final = MaxLevelFilter
