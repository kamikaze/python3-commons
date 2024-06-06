import dataclasses
import json
import logging
from datetime import datetime, date
from decimal import Decimal

import msgpack
from msgpack import ExtType

from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


def msgpack_encoder(obj):
    if isinstance(obj, Decimal):
        return ExtType(1, str(obj).encode())
    elif isinstance(obj, datetime):
        return ExtType(2, obj.isoformat().encode())
    elif isinstance(obj, date):
        return ExtType(3, obj.isoformat().encode())
    elif dataclasses.is_dataclass(obj):
        return ExtType(4, json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode())

    return f'no encoder for {obj}'


def msgpack_decoder(code, data):
    if code == 1:
        return Decimal(data.decode())
    elif code == 2:
        return datetime.fromisoformat(data.decode())
    elif code == 3:
        return date.fromisoformat(data.decode())
    elif code == 4:
        return json.loads(data)

    return f'no decoder for type {code}'


def serialize_msgpack(data) -> bytes:
    result = msgpack.packb(data, default=msgpack_encoder)

    return result


def deserialize_msgpack(data: bytes):
    result = msgpack.unpackb(data, ext_hook=msgpack_decoder)

    return result
