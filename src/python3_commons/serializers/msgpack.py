import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal

import msgpack
from msgpack import ExtType

from python3_commons.serializers.common import ExtendedType
from python3_commons.serializers.json import CustomJSONEncoder


def msgpack_encoder(obj):
    if isinstance(obj, Decimal):
        return ExtType(ExtendedType.DECIMAL, str(obj).encode())
    if isinstance(obj, datetime):
        return ExtType(ExtendedType.DATETIME, obj.isoformat().encode())
    if isinstance(obj, date):
        return ExtType(ExtendedType.DATE, obj.isoformat().encode())
    if dataclasses.is_dataclass(obj):
        return ExtType(ExtendedType.DATACLASS, json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode())

    msg = f'Objects of type {type(obj)} are not supported'

    raise TypeError(msg)


def msgpack_decoder(code, data):
    match code:
        case ExtendedType.DECIMAL:
            return Decimal(data.decode())
        case ExtendedType.DATETIME:
            return datetime.fromisoformat(data.decode())
        case ExtendedType.DATE:
            return date.fromisoformat(data.decode())
        case ExtendedType.DATACLASS:
            return json.loads(data)
        case _:
            return ExtType(code, data)


def serialize_msgpack(data) -> bytes:
    return msgpack.packb(data, default=msgpack_encoder)


def deserialize_msgpack(data: bytes):
    return msgpack.unpackb(data, ext_hook=msgpack_decoder)
