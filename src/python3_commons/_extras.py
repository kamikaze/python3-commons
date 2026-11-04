# pyright: strict
"""Guard for optional extras: raise a clear `RuntimeError` when an extra is not installed."""

from importlib.util import find_spec

__all__ = ['require']


def require(extra: str, *modules: str) -> None:
    """Raise `RuntimeError` unless every top-level module in `modules` is installed.

    Only the presence of the module is checked, so an installed dependency that fails to import
    raises its own ImportError instead of being reported as a missing extra.
    """
    if any(find_spec(module) is None for module in modules):
        msg = f'Install python3-commons[{extra}] to use this feature'
        raise RuntimeError(msg)
