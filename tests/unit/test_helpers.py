import hashlib
import shlex
from datetime import date, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import HttpUrl

from python3_commons.dates import date_from_string, date_range, datetime_from_string
from python3_commons.decimals import round_decimal
from python3_commons.helpers import SingletonMeta
from python3_commons.text import parse_string_list, to_snake_case
from python3_commons.urls import replace_origin, request_to_curl


def test_date_range() -> None:
    expected_dates = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5))
    dates = tuple(date_range(date(2024, 1, 1), date(2024, 1, 5)))

    assert dates == expected_dates


def test_singleton_meta():
    class S(metaclass=SingletonMeta):
        pass

    s1 = S()
    s2 = S()
    assert s1 is s2


def test_date_from_string():
    assert date_from_string('01.01.2024') == date(2024, 1, 1)
    assert date_from_string('2024-01-01') == date(2024, 1, 1)


def test_datetime_from_string():
    assert datetime_from_string('01.01.2024 12:00:00') == datetime(2024, 1, 1, 12, 0, 0)
    assert datetime_from_string('2024-01-01T12:00:00') == datetime(2024, 1, 1, 12, 0, 0)


def test_round_decimal():
    assert round_decimal(Decimal('1.234')) == Decimal('1.23')
    assert round_decimal(Decimal('1.235')) == Decimal('1.24')

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert round_decimal(1.234) == 1.234  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]  # Should return as is if not Decimal


def test_to_snake_case():
    assert to_snake_case('Foo Bar') == 'foo_bar'
    assert to_snake_case('  Foo  Bar  ') == 'foo_bar'

    with pytest.warns(DeprecationWarning, match='slugify'):
        assert to_snake_case('Foo-Bar!') == 'foobar'


def test_parse_string_list():
    assert parse_string_list('a, b, c') == ('a', 'b', 'c')
    assert parse_string_list(['a', 'b']) == ('a', 'b')
    assert parse_string_list('a b, c,') == ('a b', 'c')
    assert parse_string_list('') == ()
    assert parse_string_list(' , ') == ()
    assert parse_string_list('single') == ('single',)
    assert parse_string_list('["a", " b ", ""]') == ('a', 'b')


def _split(cmd: str) -> list[str]:
    return shlex.split(cmd, comments=True)


def test_request_to_curl_binary_body_does_not_raise() -> None:
    body = b'\x89PNG\r\n\x1a\n\xff\xfe'
    cmd = request_to_curl('https://x/a', method='post', data=body)
    tokens = _split(cmd)

    assert tokens[tokens.index('--data-binary') + 1] == '@body.bin'
    assert '-d' not in tokens
    assert f'{len(body)} bytes' in cmd
    assert hashlib.sha256(body).hexdigest() in cmd


def test_request_to_curl_cookies() -> None:
    cookies: SimpleCookie = SimpleCookie()
    cookies['a'] = '1'
    cookies['b'] = 'x y'
    tokens = _split(request_to_curl('https://x/a', cookies=cookies))

    assert tokens[tokens.index('-b') + 1] == 'a=1; b=x y'


def test_request_to_curl_unsupported_data_type_emits_no_body() -> None:
    tokens = _split(request_to_curl('https://x/a', method='post', data='text'))  # ty: ignore[invalid-argument-type]  # pyright: ignore[reportArgumentType]

    assert '--data-raw' not in tokens
    assert '--data-binary' not in tokens


def test_replace_origin_requires_a_host() -> None:
    hostless = cast('HttpUrl', SimpleNamespace(host=None))

    with pytest.raises(ValueError, match='must have a host'):
        replace_origin(HttpUrl('https://x/a'), hostless)


def test_replace_origin_swaps_scheme_host_and_port() -> None:
    assert str(replace_origin(HttpUrl('http://old/a/b?q=1'), HttpUrl('https://new:8443'))) == 'https://new:8443/a/b?q=1'


def test_request_to_curl_merges_query() -> None:
    tokens = _split(request_to_curl('https://x/a?x=1#frag', query={'y': 2, 'z': [3, 4]}))

    assert tokens[4] == 'https://x/a?x=1&y=2&z=3&z=4#frag'
    assert tokens[4].count('?') == 1


def test_request_to_curl_query_without_existing() -> None:
    assert _split(request_to_curl('https://x/a', query={'y': 'a b'}))[4] == 'https://x/a?y=a+b'


def test_request_to_curl_data_uses_data_raw() -> None:
    tokens = _split(request_to_curl('https://x/a', method='post', data=b'@/etc/passwd'))

    assert tokens[-2:] == ['--data-raw', '@/etc/passwd']
    assert '-d' not in tokens


def test_request_to_curl_mapping_data_and_quoting() -> None:
    tokens = _split(request_to_curl('https://x/a', method='post', data={'a': "it's @x"}))

    assert tokens[-2:] == ['--data-raw', 'a=it%27s+%40x']


def test_request_to_curl_empty_json_emitted() -> None:
    tokens = _split(request_to_curl('https://x/a', method='post', json={}))

    assert tokens[-2:] == ['-d', '{}']
    assert 'Content-Type: application/json' in tokens


def test_request_to_curl_content_type_once() -> None:
    tokens = _split(
        request_to_curl('https://x/a', method='post', headers={'content-type': 'application/json'}, json={'a': 1})
    )

    assert [t for t in tokens if t.lower().startswith('content-type:')] == ['content-type: application/json']

    tokens = _split(request_to_curl('https://x/a', method='post', json={'a': "it's"}))

    assert [t for t in tokens if t.lower().startswith('content-type:')] == ['Content-Type: application/json']
    assert tokens[-1] == '{"a": "it\'s"}'


def test_deprecated_logger_alias_resolves_to_renamed_object() -> None:
    from python3_commons import helpers  # noqa: PLC0415

    with pytest.warns(DeprecationWarning, match='1.0'):
        value = helpers.logger

    assert value is helpers._logger  # noqa: SLF001
