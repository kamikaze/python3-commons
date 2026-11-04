import dataclasses
import json
import socket
import warnings
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import pytest
from pydantic import BaseModel

from python3_commons.serializers.json import CustomJSONEncoder, dumps, loads

UID = UUID('12345678-1234-5678-1234-567812345678')


class Color(Enum):
    RED = 'red'
    ONE = 1


class Model(BaseModel):
    uid: UUID
    name: str


def enc(value):
    return json.loads(json.dumps(value, cls=CustomJSONEncoder))


def test_encoder_uuid():
    assert enc({'a': UID}) == {'a': str(UID)}


def test_encoder_sets():
    assert enc({1}) == [1]
    assert enc(frozenset({'x'})) == ['x']


def test_encoder_enum_value():
    assert enc([Color.RED, Color.ONE]) == ['red', 1]


def test_encoder_base_model():
    assert enc(Model(uid=UID, name='n')) == {'uid': str(UID), 'name': 'n'}


def test_encoder_existing_types_do_not_warn():
    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert enc([datetime(2020, 1, 2, tzinfo=UTC), date(2020, 1, 2), b'ab', Decimal('1.5'), ValueError('x')]) == [
            '2020-01-02T00:00:00+00:00',
            '2020-01-02',
            'YWI=',
            '1.5',
            'x',
        ]


def test_encoder_type_stringified_with_deprecation():
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        assert enc(int) == "<class 'int'>"

    assert record[0].filename == __file__  # attributed to the caller, not to json or the library


def test_encoder_deprecation_attributed_to_caller_through_nesting():
    # The pure-Python and C encoder paths call default() at different depths; nesting changes the depth too
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        json.dumps({'a': [{'b': int}]}, cls=CustomJSONEncoder)

    assert record[0].filename == __file__


def test_encoder_socket_stringified_with_deprecation():
    with socket.socket() as s, pytest.warns(DeprecationWarning, match='1.0'):
        assert enc(s) == str(s)


def test_encoder_unknown_raises():
    with pytest.raises(TypeError):
        enc(object())


def test_dumps_loads_round_trip():
    data = {'u': UID, 's': {1}, 'e': Color.RED, 'm': Model(uid=UID, name='n'), 'd': Decimal('1.5')}

    assert isinstance(dumps(data), bytes)
    assert loads(dumps(data)) == {
        'u': str(UID),
        's': [1],
        'e': 'red',
        'm': {'uid': str(UID), 'name': 'n'},
        'd': '1.5',
    }
    assert loads(dumps(ValueError('boom'))) == 'boom'


def test_dumps_unsupported_raises_type_error():
    with pytest.raises(TypeError):
        dumps(object())


def test_encoder_dataclass_and_bytes():
    @dataclasses.dataclass
    class Point:
        x: int
        y: bytes

    assert enc(Point(1, b'\xff')) == {'x': 1, 'y': '/w=='}
