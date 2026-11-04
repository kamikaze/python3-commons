"""Behaviour added with the helpers.py split (3.D1, slice b): date `tz=`, `slugify` and the 0.29.x warnings."""

import os
import warnings
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from python3_commons.dates import date_from_string, datetime_from_string
from python3_commons.decimals import round_decimal
from python3_commons.fs import iter_files
from python3_commons.text import slugify, to_snake_case

SLUG_CASES = [
    ('Foo Bar', 'foo_bar'),
    ('  Foo  Bar  ', 'foo_bar'),
    ('Foo-Bar!', 'foobar'),
    ('CamelCase', 'camelcase'),
    ('someHTTPValue', 'somehttpvalue'),
    ('Größe', 'gre'),
    ('naïve café', 'nave_caf'),
    ('', ''),
]


@pytest.mark.parametrize(('text', 'expected'), SLUG_CASES)
def test_slugify_keeps_the_current_to_snake_case_output(text: str, expected: str) -> None:
    assert slugify(text) == expected

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)

        assert to_snake_case(text) == expected


@pytest.mark.parametrize('text', ['CamelCase', 'someHTTPValue', 'foo-bar', 'Größe', 'Foo-Bar!'])
def test_to_snake_case_warns_when_the_1_0_result_differs(text: str) -> None:
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        to_snake_case(text)

    assert 'slugify' in str(record[0].message)
    assert record[0].filename == __file__


@pytest.mark.parametrize('text', ['Foo Bar', '  Foo  Bar  ', 'foo_bar', 'abc', ''])
def test_to_snake_case_is_silent_when_the_1_0_result_is_the_same(text: str) -> None:
    assert to_snake_case(text) == slugify(text)


def test_round_decimal_warns_on_float_only() -> None:
    assert round_decimal(Decimal('1.005')) == Decimal('1.01')

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert round_decimal(1.005) == 1.005  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]


def test_date_from_string_tz_is_accepted_and_does_not_change_the_date() -> None:
    assert date_from_string('01.02.2024', tz=UTC) == date(2024, 2, 1)
    assert date_from_string('2024-02-01', tz=UTC) == date(2024, 2, 1)


def test_datetime_from_string_tz_applies_to_naive_results_only() -> None:
    plus_two = timezone(timedelta(hours=2))

    assert datetime_from_string('01.01.2024 12:00:00') == datetime(2024, 1, 1, 12)
    assert datetime_from_string('01.01.2024 12:00:00').tzinfo is None
    assert datetime_from_string('01.01.2024 12:00:00', tz=UTC) == datetime(2024, 1, 1, 12, tzinfo=UTC)
    assert datetime_from_string('2024-01-01T12:00:00', tz=UTC).tzinfo is UTC
    assert datetime_from_string('2024-01-01T12:00:00+02:00', tz=UTC).tzinfo == plus_two


def test_date_from_string_error_names_both_formats() -> None:
    with pytest.raises(ValueError, match=r"'31/12/2024' is neither '%d.%m.%Y' nor an ISO 8601 date") as excinfo:
        date_from_string('31/12/2024')

    assert type(excinfo.value) is ValueError
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_datetime_from_string_error_names_both_formats() -> None:
    with pytest.raises(ValueError, match=r"neither '%d.%m.%Y %H:%M:%S' nor an ISO 8601 datetime") as excinfo:
        datetime_from_string('nope')

    assert type(excinfo.value) is ValueError


def _tree(root: Path) -> None:
    (root / 'a.txt').write_text('a')
    (root / '.env').write_text('e')
    (root / 'sub').mkdir()
    (root / 'sub' / 'b.txt').write_text('b')
    (root / '.git').mkdir()
    (root / '.git' / 'config').write_text('c')


def _names(root: Path, **kwargs: bool | None) -> set[str]:
    return {p.relative_to(root).as_posix() for p in iter_files(root, **kwargs)}  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]


def test_iter_files_default_warns_when_a_hidden_file_is_yielded(tmp_path: Path) -> None:
    _tree(tmp_path)

    with pytest.warns(DeprecationWarning, match='include_hidden') as record:
        assert _names(tmp_path) == {'a.txt', '.env', 'sub/b.txt'}

    assert len(record) == 1


def test_iter_files_default_is_silent_without_hidden_files_or_symlinks(tmp_path: Path) -> None:
    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / '.git').mkdir()
    (tmp_path / '.git' / 'config').write_text('c')

    assert _names(tmp_path) == {'a.txt'}


def test_iter_files_include_hidden_is_explicit_and_silent(tmp_path: Path) -> None:
    _tree(tmp_path)

    assert _names(tmp_path, include_hidden=True) == {'a.txt', '.env', 'sub/b.txt', '.git/config'}
    assert _names(tmp_path, include_hidden=False) == {'a.txt', 'sub/b.txt'}


def test_iter_files_non_recursive(tmp_path: Path) -> None:
    _tree(tmp_path)

    assert _names(tmp_path, recursive=False, include_hidden=False) == {'a.txt'}


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason='needs symlinks')
def test_iter_files_directory_symlink(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'secret.txt').write_text('s')
    (root / 'esc').symlink_to(outside, target_is_directory=True)

    with pytest.warns(DeprecationWarning, match='follow_symlinks'):
        assert _names(root) == {'esc/secret.txt'}

    assert _names(root, follow_symlinks=True) == {'esc/secret.txt'}
    assert _names(root, follow_symlinks=False) == set()


@pytest.mark.skipif(not hasattr(os, 'symlink'), reason='needs symlinks')
def test_iter_files_symlink_loop_terminates(tmp_path: Path) -> None:
    (tmp_path / 'a.txt').write_text('a')
    (tmp_path / 'loop').symlink_to(tmp_path, target_is_directory=True)

    assert _names(tmp_path, follow_symlinks=False, include_hidden=False) == {'a.txt'}

    # no loop detection: the OS stops the descent (ELOOP, about 40 levels), so the walk ends, with repeats
    names = _names(tmp_path, follow_symlinks=True, include_hidden=False)

    assert 'a.txt' in names
    assert len(names) < 100
