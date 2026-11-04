import subprocess
import sys
from pathlib import Path

import pytest

from python3_commons import helpers
from python3_commons.dates import date_from_string, date_range, datetime_from_string
from python3_commons.decimals import round_decimal
from python3_commons.decorators import log_execution_time
from python3_commons.text import parse_string_list, to_snake_case
from python3_commons.urls import replace_origin, request_to_curl

REPO_ROOT = Path(__file__).resolve().parents[2]

MOVED = {
    'date_from_string': date_from_string,
    'date_range': date_range,
    'datetime_from_string': datetime_from_string,
    'log_execution_time': log_execution_time,
    'parse_string_list': parse_string_list,
    'replace_origin': replace_origin,
    'request_to_curl': request_to_curl,
    'round_decimal': round_decimal,
    'to_snake_case': to_snake_case,
}


@pytest.mark.parametrize(('name', 'expected'), sorted(MOVED.items()))
def test_moved_name_resolves_to_the_new_object_with_a_warning(name: str, expected: object) -> None:
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        assert getattr(helpers, name) is expected

    assert record[0].filename == __file__


def test_a10_names_stay_without_warnings() -> None:
    assert helpers.SingletonMeta.__name__ == 'SingletonMeta'
    assert callable(helpers.tries)


def test_unknown_helpers_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match='no_such_name'):
        _ = helpers.no_such_name


def test_internal_modules_import_without_deprecation_warnings() -> None:
    result = subprocess.run(
        [
            sys.executable,
            '-W',
            'error::DeprecationWarning',
            '-c',
            'import python3_commons.conf, python3_commons.auth, python3_commons.api_client',
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr


def test_text_does_not_import_pydantic() -> None:
    result = subprocess.run(
        [sys.executable, '-c', 'import sys, python3_commons.text; print("pydantic" in sys.modules)'],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )

    assert result.stdout.strip() == 'False'
