import dataclasses
import json
import logging
from datetime import date, datetime
from decimal import Decimal

import msgpack
from msgpack import ExtType

from python3_commons.serializers.common import ExtendedType
from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


def msgpack_encoder(obj):
    if isinstance(obj, Decimal):
        return ExtType(ExtendedType.DECIMAL, str(obj).encode())
    elif isinstance(obj, datetime):
        return ExtType(ExtendedType.DATETIME, obj.isoformat().encode())
    elif isinstance(obj, date):
        return ExtType(ExtendedType.DATE, obj.isoformat().encode())
    elif dataclasses.is_dataclass(obj):
        return ExtType(ExtendedType.DATACLASS, json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode())

    return f'no encoder for {obj}'


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

    return f'no decoder for type {code}'


def serialize_msgpack(data) -> bytes:
    result = msgpack.packb(data, default=msgpack_encoder)

    return result


def deserialize_msgpack(data: bytes):
    result = msgpack.unpackb(data, ext_hook=msgpack_decoder)

    return result
