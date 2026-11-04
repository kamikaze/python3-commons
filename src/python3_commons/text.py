# pyright: strict
import re
import warnings
from collections.abc import Iterable
from json import loads

__all__ = ['parse_string_list', 'slugify', 'to_snake_case']

# camel humps: 'camelCase' -> 'camel_Case', 'someHTTPValue' -> 'some_HTTP_Value'
_HUMP = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')


def slugify(s: str) -> str:
    """Lowercase ASCII slug: whitespace becomes underscores, everything but [a-z0-9_] is dropped."""
    # Lowercase and strip whitespace
    s = s.strip().lower()

    # Replace all whitespace with underscores
    s = re.sub(r'\s+', '_', s)

    # Keep only alphanumeric and underscore characters
    s = re.sub(r'[^a-z0-9_]', '', s)

    # Collapse consecutive underscores
    return re.sub(r'_+', '_', s)


def _snake_case_1_0(s: str) -> str:
    """What `to_snake_case` returns in 1.0: camel humps split, other characters are separators, Unicode kept."""
    s = _HUMP.sub('_', s.strip()).lower()

    return re.sub(r'_+', '_', re.sub(r'[\W_]+', '_', s))


def to_snake_case(s: str) -> str:
    result = slugify(s)

    if result != _snake_case_1_0(s):
        warnings.warn(
            f'to_snake_case({s!r}) changes in 1.0 (camel humps are split, Unicode letters are kept); '
            'use slugify() to keep the current output',
            DeprecationWarning,
            stacklevel=2,
        )

    return result


def parse_string_list(value: str | Iterable[str], /) -> tuple[str, ...]:
    if isinstance(value, str):
        stripped = value.strip()

        items = loads(stripped) if stripped.startswith('[') else stripped.split(',')
    else:
        items = value

    return tuple(item for item in (str(i).strip() for i in items) if item)
