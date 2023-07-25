from datetime import datetime, date
from decimal import Decimal

from python3_commons.serializers import msgpack


def test_encode_decode_dict_to_msgpack(data_dict):
    expected_result = {
        'A': 1,
        'B': 'B',
        'C': None,
        'D': datetime(2023, 7, 25, 1, 2, 3),
        'E': date(2023, 7, 24),
        'F': Decimal('1.23'),
    }
    binary_data = msgpack.serialize_msgpack(data_dict)

    assert msgpack.deserialize_msgpack(binary_data) == expected_result


def test_encode_decode_dataclass_to_msgpack(data_dataclass):
    expected_data = {'a': 1, 'b': 'B', 'c': None, 'd': '2023-07-25T01:02:03', 'e': '2023-07-24', 'f': '1.23'}
    binary_data = msgpack.serialize_msgpack(data_dataclass)

    assert msgpack.deserialize_msgpack(binary_data) == expected_data
