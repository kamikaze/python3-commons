from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from msgspec import msgpack
from msgspec.msgpack import Ext, encode
from pydantic import BaseModel

from python3_commons.serializers.common import ExtendedType
from python3_commons.serializers.json import CustomJSONEncoder


def enc_hook(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return Ext(int(ExtendedType.DECIMAL), str(obj).encode())
    if isinstance(obj, datetime):
        return Ext(int(ExtendedType.DATETIME), obj.isoformat().encode())
    if isinstance(obj, date):
        return Ext(int(ExtendedType.DATE), obj.isoformat().encode())
    if dataclasses.is_dataclass(obj):
        return Ext(
            int(ExtendedType.DATACLASS),
            json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode(),
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


def _decode[T](decoder: msgpack.Decoder, data: bytes, data_type: type[T] | None) -> T | Any:
    if data_type is None:
        return decoder.decode(data)

    if issubclass(data_type, BaseModel):
        return data_type.model_validate(decoder.decode(data))

    return msgpack.decode(data, type=data_type, ext_hook=decoder.ext_hook)


def serialize_msgpack_native(data: Any) -> bytes:
    if isinstance(data, BaseModel):
        data = data.model_dump()

    return encode(data)


def deserialize_msgpack_native[T](data: bytes, data_type: type[T] | None = None) -> T | Any:
    return _decode(MSGPACK_DECODER_NATIVE, data, data_type)


def serialize_msgpack(data: Any) -> bytes:
    if isinstance(data, BaseModel):
        data = data.model_dump()

    return MSGPACK_ENCODER.encode(data)


def deserialize_msgpack[T](data: bytes, data_type: type[T] | None = None) -> T | Any:
    return _decode(MSGPACK_DECODER, data, data_type)
