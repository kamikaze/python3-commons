"""Annotations of every `__all__` name resolve at runtime (convention 9).

`typing.get_type_hints`, FastAPI, pydantic `validate_call` and doc generators evaluate annotations, so a public
signature must not name a TYPE_CHECKING-only type. The only exception is `CROSS_EXTRA`: names whose annotations
use types of an extra the module does not require. They are resolved against that extra's namespace.
"""

import annotationlib
import ast
import importlib
import inspect
import typing
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.contract import snapshot

# `<module>:<name>` -> module whose namespace resolves the annotations, as a localns. Empty: every module imports
# the types its public signatures use at runtime. A later item that adds a name annotated with TYPE_CHECKING-only
# types of another extra adds an entry here, resolved with the FORWARDREF format.
CROSS_EXTRA: dict[str, str] = {}

# No `__all__` (A10, convention 17): the two modules stay byte-identical, so they are not in PUBLIC_ALL either.
_SKIPPED = frozenset({'python3_commons.serializers.msgpack', 'python3_commons.db.helpers'})

_CASES = [(module, name) for module in sorted(snapshot.PUBLIC_ALL) for name in sorted(snapshot.PUBLIC_ALL[module])]


def _resolve(obj: Callable[..., object] | type, *, fmt: annotationlib.Format, extra: ModuleType | None) -> None:
    if extra is None:
        typing.get_type_hints(obj, format=fmt)
    else:
        typing.get_type_hints(obj, localns=vars(extra), format=fmt)

    if callable(obj) and not isinstance(obj, type):
        inspect.signature(obj, annotation_format=fmt)


def _members(obj: type) -> list[Callable[..., object]]:
    found: list[Callable[..., object]] = []

    for member in vars(obj).values():
        target = member.fget if isinstance(member, property) else member

        if isinstance(target, (staticmethod, classmethod)):
            target = target.__func__

        if inspect.isfunction(target):
            found.append(target)

    return found


@pytest.mark.parametrize(('module_name', 'name'), _CASES)
def test_public_annotations_resolve_at_runtime(module_name: str, name: str) -> None:
    assert module_name not in _SKIPPED

    if name in snapshot.STAR_IMPORT_DEPRECATED.get(module_name, frozenset()):
        pytest.skip('deprecated name: accessing it warns, and it is checked under its new home')

    obj = getattr(importlib.import_module(module_name), name)

    if not (inspect.isfunction(obj) or inspect.isclass(obj)):
        pytest.skip('not a function or class: nothing to evaluate')

    key = f'{module_name}:{name}'
    extra = importlib.import_module(CROSS_EXTRA[key]) if key in CROSS_EXTRA else None
    fmt = annotationlib.Format.FORWARDREF if extra is not None else annotationlib.Format.VALUE

    if inspect.isclass(obj):
        # only this package's classes: a third-party base (SQLAlchemy, zeep) may have TYPE_CHECKING-only annotations
        for cls in (c for c in obj.__mro__ if c.__module__.startswith('python3_commons')):
            annotationlib.get_annotations(cls, format=fmt)

        targets = _members(obj)
    else:
        targets = [obj]

    for target in targets:
        _resolve(target, fmt=fmt, extra=extra)


def test_skipped_modules_have_no_public_all_snapshot() -> None:
    assert _SKIPPED.isdisjoint(snapshot.PUBLIC_ALL)


def test_forwardref_format_resolves_a_type_checking_only_annotation() -> None:
    """The mechanism for the cross-extra allowlist: VALUE raises NameError, FORWARDREF with a localns resolves."""
    namespace: dict[str, object] = {}
    exec('def f(x: Extra | None) -> Extra: ...', namespace)  # noqa: S102
    f = typing.cast('Callable[..., object]', namespace['f'])

    with pytest.raises(NameError):
        typing.get_type_hints(f, format=annotationlib.Format.VALUE)

    class Extra: ...

    hints = typing.get_type_hints(f, localns={'Extra': Extra}, format=annotationlib.Format.FORWARDREF)

    assert hints['return'] is Extra
    assert hints['x'] == Extra | None
    assert inspect.signature(f, annotation_format=annotationlib.Format.FORWARDREF).return_annotation is not None


def test_no_module_postpones_annotation_evaluation() -> None:
    """3.14 defers annotations by default (PEP 749); `from __future__ import annotations` only hides NameErrors."""
    src = Path(__file__).resolve().parents[2] / 'src' / 'python3_commons'
    offenders = [
        str(path.relative_to(src))
        for path in sorted(src.rglob('*.py'))
        if any(
            isinstance(node, ast.ImportFrom) and node.module == '__future__'
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
        )
    ]

    assert offenders == []
