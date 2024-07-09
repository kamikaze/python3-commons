import dataclasses
import json
import logging
import struct
from _decimal import Decimal
from datetime import datetime, date
from typing import Any

from msgspec import msgpack
from msgspec.msgpack import Ext

from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


def enc_hook(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return Ext(1, struct.pack('b', str(obj).encode()))
    elif isinstance(obj, datetime):
        return Ext(2, struct.pack('b', obj.isoformat().encode()))
    elif isinstance(obj, date):
        return Ext(3, struct.pack('b', obj.isoformat().encode()))
    elif dataclasses.is_dataclass(obj):
        return Ext(4, struct.pack('b', json.dumps(dataclasses.asdict(obj), cls=CustomJSONEncoder).encode()))

    raise NotImplementedError(f'Objects of type {type(obj)} are not supported')


def ext_hook(code: int, data: memoryview) -> Any:
    if code == 1:
        return Decimal(data.tobytes().decode())
    elif code == 2:
        return datetime.fromisoformat(data.tobytes().decode())
    elif code == 3:
        return date.fromisoformat(data.tobytes().decode())
    elif code == 4:
        return json.loads(data.tobytes())

    raise NotImplementedError(f'Extension type code {code} is not supported')


MSGPACK_ENCODER = msgpack.Encoder(enc_hook=enc_hook)
MSGPACK_DECODER = msgpack.Decoder(ext_hook=ext_hook)


def serialize_msgpack(data) -> bytes:
    result = MSGPACK_ENCODER.encode(data)

    return result


def deserialize_msgpack(data: bytes, data_type=None):
    if data_type:
        result = msgpack.decode(data, type=data_type)
    else:
        result = MSGPACK_DECODER.decode(data)

    return result
