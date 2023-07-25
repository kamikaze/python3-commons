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
    logger.debug('Encoding object', extra={'obj': obj})

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
    logger.debug(f'Decoding object with {code=}', extra={'data': data})

    if code == 1:
        return Decimal(data.tobytes().decode())
    elif code == 2:
        return datetime.fromisoformat(data.tobytes().decode())
    elif code == 3:
        return date.fromisoformat(data.tobytes().decode())
    elif code == 4:
        return json.loads(data.tobytes())

    raise NotImplementedError(f'Extension type code {code} is not supported')


def serialize_msgpack(data) -> bytes:
    logger.debug('Serializing to msgpack', extra={'data': data})

    result = msgpack.encode(data, enc_hook=enc_hook)

    logger.debug('Serialized to msgpack', extra={'result': result})

    return result


def deserialize_msgpack(data: bytes, data_type=None):
    logger.debug('De-serializing from msgpack', extra={'data': data})

    if data_type:
        result = msgpack.decode(data, type=data_type)
    else:
        result = msgpack.decode(data, ext_hook=ext_hook)

    logger.debug('De-serialized from msgpack', extra={'result': result})

    return result
