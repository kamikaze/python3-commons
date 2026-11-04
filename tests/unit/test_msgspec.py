# ruff: noqa: SLF001
from datetime import date, datetime
from decimal import Decimal

import pytest
from msgspec import msgpack as mpk

from python3_commons.serializers import msgspec
from python3_commons.serializers.common import ExtendedType


def test_encode_decode_dict_to_msgpack(data_dict) -> None:
    """
    A dict of datetime, date and Decimal values is encoded through enc_hook and decodes (untyped) to the
    string forms of those values; the round trip is deliberately lossy without a target type.
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


def test_enc_hook_pydantic_and_unsupported(pydantic_struct) -> None:
    assert msgspec._enc_hook(pydantic_struct) == pydantic_struct.model_dump(mode='python')

    with pytest.raises(NotImplementedError, match='not supported'):
        msgspec._enc_hook(object())


def test_ext_hook_legacy_codes(data_dataclass) -> None:
    dec = Decimal('123.456')
    assert msgspec._ext_hook(ExtendedType.DECIMAL, memoryview(str(dec).encode())) == dec

    dt = datetime(2026, 9, 30, 12, 0, 0)
    assert msgspec._ext_hook(ExtendedType.DATETIME, memoryview(dt.isoformat().encode())) == dt

    d = date(2026, 9, 30)
    assert msgspec._ext_hook(ExtendedType.DATE, memoryview(d.isoformat().encode())) == d

    assert msgspec._ext_hook(ExtendedType.DATACLASS, memoryview(b'{"a": 1}')) == {'a': 1}


def test_unknown_ext_code_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match='Extension type code'):
        msgspec._ext_hook(999, memoryview(b'test'))

    data = mpk.encode(mpk.Ext(99, b'x'))

    with pytest.raises(NotImplementedError, match='Extension type code'):
        msgspec.deserialize_msgpack(data)


def test_legacy_ext_coded_data_decodes() -> None:
    data = mpk.encode([mpk.Ext(int(ExtendedType.DECIMAL), b'1.25'), mpk.Ext(int(ExtendedType.DATE), b'2023-07-24')])

    assert msgspec.deserialize_msgpack(data) == [Decimal('1.25'), date(2023, 7, 24)]


def test_unsupported_type_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match='not supported'):
        msgspec.serialize_msgpack(object())


def test_unsupported_type_native_raises_type_error() -> None:
    with pytest.raises(TypeError):
        msgspec.serialize_msgpack_native(object())


@pytest.mark.parametrize(
    ('serialize', 'deserialize'),
    [
        (msgspec.serialize_msgpack, msgspec.deserialize_msgpack),
        (msgspec.serialize_msgpack_native, msgspec.deserialize_msgpack_native),
    ],
)
class TestGenericRoundTrips:
    def test_list_of_struct(self, serialize, deserialize, msgspec_struct) -> None:
        value = [msgspec_struct, msgspec_struct]

        assert deserialize(serialize(value), list[msgspec_struct.__class__]) == value  # ty: ignore[invalid-type-form]

    def test_dict_of_models(self, serialize, deserialize, pydantic_struct) -> None:
        value = {'k': pydantic_struct}

        assert deserialize(serialize(value), dict[str, pydantic_struct.__class__]) == value  # ty: ignore[invalid-type-form]

    def test_list_of_models(self, serialize, deserialize, pydantic_struct) -> None:
        value = [pydantic_struct, pydantic_struct]

        assert deserialize(serialize(value), list[pydantic_struct.__class__]) == value  # ty: ignore[invalid-type-form]

    def test_optional_model(self, serialize, deserialize, pydantic_struct) -> None:
        tp = pydantic_struct.__class__ | None

        assert deserialize(serialize(pydantic_struct), tp) == pydantic_struct
        assert deserialize(serialize(None), tp) is None

    def test_union_falls_back_to_type_adapter(self, serialize, deserialize, pydantic_struct) -> None:
        tp = pydantic_struct.__class__ | int

        assert deserialize(serialize(pydantic_struct), tp) == pydantic_struct
        assert deserialize(serialize(5), tp) == 5

    def test_nested_model_in_dict_encodes(self, serialize, deserialize, pydantic_struct) -> None:
        decoded = deserialize(serialize({'m': pydantic_struct}))

        assert decoded['m']['a'] == pydantic_struct.a
        assert decoded['m']['sub']['name'] == pydantic_struct.sub.name

    def test_plain_generics(self, serialize, deserialize) -> None:
        assert deserialize(serialize([1, 2]), list[int]) == [1, 2]
        assert deserialize(serialize({'a': 1}), dict[str, int]) == {'a': 1}
        assert deserialize(serialize(None), int | None) is None

    def test_untyped_returns_plain_values(self, serialize, deserialize) -> None:
        decoded = deserialize(serialize({'amount': Decimal('1.5'), 'tags': {'a'}}))

        assert decoded == {'amount': '1.5', 'tags': ['a']}


@pytest.mark.parametrize(
    ('old', 'new'),
    [
        ('enc_hook', '_enc_hook'),
        ('ext_hook', '_ext_hook'),
        ('MSGPACK_ENCODER', '_MSGPACK_ENCODER'),
        ('MSGPACK_ENCODER_NATIVE', '_MSGPACK_ENCODER_NATIVE'),
        ('MSGPACK_DECODER', '_MSGPACK_DECODER'),
        ('MSGPACK_DECODER_NATIVE', '_MSGPACK_DECODER_NATIVE'),
    ],
)
def test_deprecated_internal_alias_resolves_to_renamed_object(old: str, new: str) -> None:
    with pytest.warns(DeprecationWarning, match='1.0'):
        value = getattr(msgspec, old)

    assert value is getattr(msgspec, new)


def test_unknown_attribute_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        msgspec.does_not_exist  # noqa: B018


def test_pydantic_dec_hook_rejects_non_model_types() -> None:
    with pytest.raises(NotImplementedError, match='not supported'):
        msgspec._pydantic_dec_hook(Decimal, '1')
