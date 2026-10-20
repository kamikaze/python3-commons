from datetime import date, datetime
from decimal import Decimal

import msgspec.json as msgspec_json
import pytest

from python3_commons.serializers import msgspec
from python3_commons.serializers.common import ExtendedType


def test_encode_decode_dict_to_msgpack(data_dict) -> None:
    """
    enc_hook is not being called on complex types like dict
    """
    expected_result = {
        'A': 1,
        'B': 'B',
        'C': None,
        'D': '2023-07-25T01:02:03',
        'E': '2023-07-24',
        'F': '1.23',
    }
    binary_data = msgspec.serialize_msgpack(data_dict)
    deserialized_data = msgspec.deserialize_msgpack(binary_data)

    assert deserialized_data == expected_result


def test_encode_decode_dataclass_to_msgpack(data_dataclass) -> None:
    binary_data = msgspec.serialize_msgpack(data_dataclass)

    assert msgspec.deserialize_msgpack(binary_data, data_type=data_dataclass.__class__) == data_dataclass


def test_encode_decode_struct_to_msgpack(msgspec_struct) -> None:
    binary_data = msgspec.serialize_msgpack(msgspec_struct)
    decoded_struct = msgspec.deserialize_msgpack(binary_data, msgspec_struct.__class__)

    assert decoded_struct == msgspec_struct


def test_encode_decode_struct_to_msgpack_native(msgspec_struct) -> None:
    binary_data = msgspec.serialize_msgpack_native(msgspec_struct)
    decoded_struct = msgspec.deserialize_msgpack_native(binary_data, msgspec_struct.__class__)

    assert decoded_struct == msgspec_struct


def test_encode_decode_decimal_to_msgpack() -> None:
    value = Decimal('1.2345')
    binary_data = msgspec.serialize_msgpack(value)
    decoded_value = msgspec.deserialize_msgpack(binary_data, Decimal)

    assert decoded_value == value


def test_encode_decode_str_to_msgpack() -> None:
    value = '1.2345'
    binary_data = msgspec.serialize_msgpack(value)
    decoded_value = msgspec.deserialize_msgpack(binary_data)

    assert decoded_value == value


def test_encode_decode_pydantic_struct_to_msgpack(pydantic_struct) -> None:
    binary_data = msgspec.serialize_msgpack(pydantic_struct)
    decoded_struct = msgspec.deserialize_msgpack(binary_data, pydantic_struct.__class__)

    assert decoded_struct == pydantic_struct


def test_encode_decode_pydantic_struct_to_msgpack_native(pydantic_struct) -> None:
    binary_data = msgspec.serialize_msgpack_native(pydantic_struct)
    decoded_struct = msgspec.deserialize_msgpack_native(binary_data, pydantic_struct.__class__)

    assert decoded_struct == pydantic_struct


def test_encode_decode_struct_to_json(msgspec_struct) -> None:
    data = msgspec_json.encode(msgspec_struct)
    struct = msgspec_json.decode(data, type=msgspec_struct.__class__)

    assert struct == msgspec_struct


def test_enc_hook_and_ext_hook(data_dataclass) -> None:
    # Decimal
    dec = Decimal('123.456')
    ext_dec = msgspec.enc_hook(dec)
    assert ext_dec.code == ExtendedType.DECIMAL
    assert msgspec.ext_hook(ext_dec.code, memoryview(ext_dec.data)) == dec

    # Datetime
    dt = datetime(2026, 9, 30, 12, 0, 0)
    ext_dt = msgspec.enc_hook(dt)
    assert ext_dt.code == ExtendedType.DATETIME
    assert msgspec.ext_hook(ext_dt.code, memoryview(ext_dt.data)) == dt

    # Date
    d = date(2026, 9, 30)
    ext_d = msgspec.enc_hook(d)
    assert ext_d.code == ExtendedType.DATE
    assert msgspec.ext_hook(ext_d.code, memoryview(ext_d.data)) == d

    # Dataclass
    ext_dc = msgspec.enc_hook(data_dataclass)
    assert ext_dc.code == ExtendedType.DATACLASS
    decoded_dc = msgspec.ext_hook(ext_dc.code, memoryview(ext_dc.data))
    assert decoded_dc['a'] == data_dataclass.a
    assert decoded_dc['b'] == data_dataclass.b

    # Unsupported type raises NotImplementedError
    with pytest.raises(NotImplementedError, match='not supported'):
        msgspec.enc_hook(object())

    # Unsupported extension code raises NotImplementedError
    with pytest.raises(NotImplementedError, match='Extension type code'):
        msgspec.ext_hook(999, memoryview(b'test'))
