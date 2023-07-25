from python3_commons.serializers import msgspec


def test_encode_decode_dict_to_msgpack(data_dict):
    expected_result = {
        'A': 1,
        'B': 'B',
        'C': None,
        'D': '2023-07-25T01:02:03',
        'E': '2023-07-24',
        'F': '1.23',
    }
    binary_data = msgspec.serialize_msgpack(data_dict)

    assert msgspec.deserialize_msgpack(binary_data) == expected_result


def test_encode_decode_dataclass_to_msgpack(data_dataclass):
    binary_data = msgspec.serialize_msgpack(data_dataclass)
    assert msgspec.deserialize_msgpack(binary_data, data_type=data_dataclass.__class__) == data_dataclass


def test_encode_decode_struct_to_msgpack(data_struct):
    binary_data = msgspec.serialize_msgpack(data_struct)

    assert msgspec.deserialize_msgpack(binary_data, data_type=data_struct.__class__) == data_struct
