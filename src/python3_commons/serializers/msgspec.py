import logging

from msgspec import msgpack

logger = logging.getLogger(__name__)


def serialize_msgpack(data) -> bytes:
    logger.debug('Serializing to msgpack', extra={'data': data})

    result = msgpack.encode(data)

    logger.debug('Serialized to msgpack', extra={'result': result})

    return result


def deserialize_msgpack(data: bytes, data_type=None):
    logger.debug('De-serializing from msgpack', extra={'data': data})

    if data_type:
        result = msgpack.decode(data, type=data_type)
    else:
        result = msgpack.decode(data)

    logger.debug('De-serialized from msgpack', extra={'result': result})

    return result
