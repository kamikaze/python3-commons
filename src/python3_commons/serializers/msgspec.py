import dataclasses
import json
import logging
import struct
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar

from msgspec import msgpack
from msgspec.msgpack import Ext, encode
from pydantic import BaseModel

from python3_commons.serializers.common import ExtendedType
from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)
T = TypeVar('T')


def enc_hook(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return Ext(ExtendedType.DECIMAL, struct.pack('b', str(obj).encode()))
    elif isinstance(obj, datetime):
        return Ext(ExtendedType.DATETIME, struct.pack('b', obj.isoformat().encode()))
    elif isinstance(obj, date):
        return Ext(ExtendedType.DATE, struct.pack('b', obj.isoformat().encode()))
    elif dataclasses.is_dataclass(obj):
        return Ext(
            ExtendedType.DATACLASS,
            struct.pack('b', json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode()),
        )

    msg = f'Objects of type {type(obj)} are not supported'

    raise NotImplementedError(msg)


def ext_hook(code: int, data: memoryview) -> Any:
    match code:
        case ExtendedType.DECIMAL:
            return Decimal(data.tobytes().decode())
        case ExtendedType.DATETIME:
            return datetime.fromisoformat(data.tobytes().decode())
        case ExtendedType.DATE:
            return date.fromisoformat(data.tobytes().decode())
        case ExtendedType.DATACLASS:
            return json.loads(data.tobytes())
        case _:
            msg = f'Extension type code {code} is not supported'

            raise NotImplementedError(msg)


MSGPACK_ENCODER = msgpack.Encoder(enc_hook=enc_hook)
MSGPACK_DECODER = msgpack.Decoder(ext_hook=ext_hook)
MSGPACK_DECODER_NATIVE = msgpack.Decoder()


def serialize_msgpack_native(data: Any) -> bytes:
    if isinstance(data, BaseModel):
        data = data.model_dump()

    result = encode(data)

    return result


def deserialize_msgpack_native[T](data: bytes, data_type: type[T] | None = None) -> T | Any:
    if data_type:
        if issubclass(data_type, BaseModel):
            decoded = MSGPACK_DECODER_NATIVE.decode(data)
            result = data_type.model_validate(decoded)
        else:
            result = msgpack.decode(data, type=data_type)
    else:
        result = MSGPACK_DECODER_NATIVE.decode(data)

    return result


def serialize_msgpack(data: Any) -> bytes:
    if isinstance(data, BaseModel):
        data = data.model_dump()

    result = MSGPACK_ENCODER.encode(data)

    return result


def deserialize_msgpack[T](data: bytes, data_type: type[T] | None = None) -> T | Any:
    if data_type:
        if issubclass(data_type, BaseModel):
            decoded = MSGPACK_DECODER.decode(data)
            result = data_type.model_validate(decoded)
        else:
            result = msgpack.decode(data, type=data_type)
    else:
        result = MSGPACK_DECODER.decode(data)

    return result
