import logging
from decimal import Decimal

import msgpack
from msgpack import ExtType


logger = logging.getLogger(__name__)


def msgpack_encoder(obj):
    logger.debug('Encoding object', extra={'obj': obj})

    if isinstance(obj, Decimal):
        return ExtType(1, str(obj).encode())

    return f'no encoder for {obj}'


def msgpack_decoder(code, data):
    logger.debug(f'Decoding object with {code=}', extra={'data': data})

    if code == 1:
        return Decimal(data.decode())

    return f'no decoder for type {code}'


def serialize_msgpack(data) -> bytes:
    logger.debug('Serializing to msgpack', extra={'data': data})

    result = msgpack.packb(data, default=msgpack_encoder)

    logger.debug('Serialized to msgpack', extra={'result': result})

    return result


def deserialize_msgpack(data: bytes):
    logger.debug('De-serializing from msgpack', extra={'data': data})

    result = msgpack.unpackb(data, ext_hook=msgpack_decoder)

    logger.debug('De-serialized from msgpack', extra={'result': result})

    return result
