import contextlib
import functools
import importlib
import logging
import threading
import warnings
from abc import ABCMeta
from collections import defaultdict
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    # deprecated alias of the internal _logger, served by __getattr__ below
    logger: logging.Logger

    from python3_commons.dates import date_from_string as date_from_string  # noqa: PLC0414
    from python3_commons.dates import date_range as date_range  # noqa: PLC0414
    from python3_commons.dates import datetime_from_string as datetime_from_string  # noqa: PLC0414
    from python3_commons.decimals import round_decimal as round_decimal  # noqa: PLC0414
    from python3_commons.decorators import log_execution_time as log_execution_time  # noqa: PLC0414
    from python3_commons.text import parse_string_list as parse_string_list  # noqa: PLC0414
    from python3_commons.text import to_snake_case as to_snake_case  # noqa: PLC0414
    from python3_commons.urls import replace_origin as replace_origin  # noqa: PLC0414
    from python3_commons.urls import request_to_curl as request_to_curl  # noqa: PLC0414

# The moved names stay in __all__ until 1.0 so that a star-import keeps binding them; each one warns on access.
__all__ = [
    'SingletonMeta',
    'date_from_string',
    'date_range',
    'datetime_from_string',
    'log_execution_time',
    'parse_string_list',
    'replace_origin',
    'request_to_curl',
    'round_decimal',
    'to_snake_case',
    'tries',
]
_logger = logging.getLogger(__name__)

_MOVED: dict[str, str] = {
    'date_from_string': 'python3_commons.dates',
    'date_range': 'python3_commons.dates',
    'datetime_from_string': 'python3_commons.dates',
    'log_execution_time': 'python3_commons.decorators',
    'parse_string_list': 'python3_commons.text',
    'replace_origin': 'python3_commons.urls',
    'request_to_curl': 'python3_commons.urls',
    'round_decimal': 'python3_commons.decimals',
    'to_snake_case': 'python3_commons.text',
}


def __getattr__(name: str) -> Any:
    if name == 'logger':
        warnings.warn(
            'python3_commons.helpers.logger is internal and deprecated; it is removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

        return _logger

    if (module_name := _MOVED.get(name)) is None:
        msg = f'module {__name__!r} has no attribute {name!r}'
        raise AttributeError(msg)

    warnings.warn(
        f'python3_commons.helpers.{name} moved to {module_name}.{name}; the helpers.{name} alias is removed in 1.0',
        DeprecationWarning,
        stacklevel=2,
    )

    return getattr(importlib.import_module(module_name), name)


class SingletonMeta(ABCMeta):
    """
    A metaclass that creates a Singleton base class when called.
    """

    _instances: ClassVar[MutableMapping] = {}
    _locks: ClassVar[defaultdict] = defaultdict(threading.Lock)

    def __call__(cls, *args, **kwargs):
        try:
            return cls._instances[cls]
        except KeyError:
            with cls._locks[cls]:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)

                return cls._instances[cls]


def tries(times):
    def func_wrapper(f):
        @functools.wraps(f)
        async def wrapper(*args, **kwargs):
            for _ in range(times - 1):
                with contextlib.suppress(Exception):
                    return await f(*args, **kwargs)

            return await f(*args, **kwargs)

        return wrapper

    return func_wrapper
