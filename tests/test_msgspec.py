from decimal import Decimal

from python3_commons.serializers import msgspec


def test_encode_decode_dict_to_msgpack(data_dict):
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


def test_encode_decode_dataclass_to_msgpack(data_dataclass):
    binary_data = msgspec.serialize_msgpack(data_dataclass)

    assert msgspec.deserialize_msgpack(binary_data, data_type=data_dataclass.__class__) == data_dataclass


def test_encode_decode_struct_to_msgpack(msgspec_struct):
    binary_data = msgspec.serialize_msgpack(msgspec_struct)
    decoded_struct = msgspec.deserialize_msgpack(binary_data, msgspec_struct.__class__)

    assert decoded_struct == msgspec_struct


def test_encode_decode_struct_to_msgpack_native(msgspec_struct):
    binary_data = msgspec.serialize_msgpack_native(msgspec_struct)
    decoded_struct = msgspec.deserialize_msgpack_native(binary_data, msgspec_struct.__class__)

    assert decoded_struct == msgspec_struct


def test_encode_decode_decimal_to_msgpack():
    value = Decimal('1.2345')
    binary_data = msgspec.serialize_msgpack(value)
    decoded_value = msgspec.deserialize_msgpack(binary_data, Decimal)

    assert decoded_value == value


def test_encode_decode_str_to_msgpack():
    value = '1.2345'
    binary_data = msgspec.serialize_msgpack(value)
    decoded_value = msgspec.deserialize_msgpack(binary_data)

    assert decoded_value == value


def test_encode_decode_pydantic_struct_to_msgpack(pydantic_struct):
    binary_data = msgspec.serialize_msgpack(pydantic_struct)
    decoded_struct = msgspec.deserialize_msgpack(binary_data, pydantic_struct.__class__)

    assert decoded_struct == pydantic_struct


def test_encode_decode_pydantic_struct_to_msgpack_native(pydantic_struct):
    binary_data = msgspec.serialize_msgpack_native(pydantic_struct)
    decoded_struct = msgspec.deserialize_msgpack_native(binary_data, pydantic_struct.__class__)

    assert decoded_struct == pydantic_struct
