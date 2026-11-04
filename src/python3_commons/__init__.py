import warnings
from typing import TYPE_CHECKING, Any

__all__ = ['__version__']

if TYPE_CHECKING:
    from types import ModuleType

    __version__: str
    dist_name: str  # deprecated, removed in 1.0
    importlib: ModuleType  # deprecated, removed in 1.0


def _resolve_version() -> str:
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    try:
        return version('python3-commons')
    except PackageNotFoundError:
        return 'unknown'


def __getattr__(name: str) -> Any:
    """Resolve `__version__` on first access; serve the leaked `dist_name` and `importlib` with a warning."""
    if name == '__version__':
        value = globals()['__version__'] = _resolve_version()

        return value

    if name in {'dist_name', 'importlib'}:
        warnings.warn(
            f'python3_commons.{name} is deprecated and will be removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

        if name == 'dist_name':
            return __name__

        import importlib.metadata  # noqa: PLC0415

        return importlib

    msg = f'module {__name__!r} has no attribute {name!r}'

    raise AttributeError(msg)
