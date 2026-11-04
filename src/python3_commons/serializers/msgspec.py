import json
import warnings
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import TYPE_CHECKING, Any, overload

from msgspec import msgpack
from pydantic import BaseModel, TypeAdapter

from python3_commons.serializers.common import ExtendedType

__all__ = [
    'deserialize_msgpack',
    'deserialize_msgpack_native',
    'serialize_msgpack',
    'serialize_msgpack_native',
]


def _enc_hook(obj: Any) -> Any:
    """
    Called by msgspec only for types it cannot encode itself (it handles Decimal, datetime, date, UUID, Enum, set and
    dataclasses natively). Pydantic models are dumped to plain Python values, which msgspec then encodes.
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='python')

    msg = f'Objects of type {type(obj)} are not supported'

    raise NotImplementedError(msg)


def _native_enc_hook(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='python')

    msg = f'Encoding objects of type {type(obj).__name__} is unsupported'

    raise TypeError(msg)


def _ext_hook(code: int, data: memoryview) -> Any:
    """Decodes the extension types written by earlier versions of this library."""
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


def _pydantic_dec_hook(tp: type, obj: Any) -> Any:
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return tp.model_validate(obj)

    msg = f'Objects of type {tp} are not supported'

    raise NotImplementedError(msg)


_MSGPACK_ENCODER = msgpack.Encoder(enc_hook=_enc_hook)
_MSGPACK_ENCODER_NATIVE = msgpack.Encoder(enc_hook=_native_enc_hook)
_MSGPACK_DECODER = msgpack.Decoder(ext_hook=_ext_hook)
_MSGPACK_DECODER_NATIVE = msgpack.Decoder()


@lru_cache(maxsize=256)
def _typed_decoder(data_type: Any, *, native: bool) -> msgpack.Decoder[Any] | None:
    try:
        return msgpack.Decoder(
            data_type,
            dec_hook=_pydantic_dec_hook,
            ext_hook=None if native else _ext_hook,
        )
    except TypeError:
        return None


@lru_cache(maxsize=256)
def _type_adapter(data_type: Any) -> TypeAdapter[Any]:
    return TypeAdapter(data_type)


def _decode(data: bytes, data_type: Any, *, native: bool) -> Any:
    untyped = _MSGPACK_DECODER_NATIVE if native else _MSGPACK_DECODER

    if data_type is None:
        return untyped.decode(data)

    if isinstance(data_type, type) and issubclass(data_type, BaseModel):
        return data_type.model_validate(untyped.decode(data))

    decoder = _typed_decoder(data_type, native=native)

    if decoder is not None:
        return decoder.decode(data)

    return _type_adapter(data_type).validate_python(untyped.decode(data))


def serialize_msgpack_native(data: Any) -> bytes:
    return _MSGPACK_ENCODER_NATIVE.encode(data)


@overload
def deserialize_msgpack_native(data: bytes, data_type: None = None) -> Any: ...
@overload
def deserialize_msgpack_native[T](data: bytes, data_type: type[T]) -> T: ...
def deserialize_msgpack_native(data: bytes, data_type: Any = None) -> Any:
    return _decode(data, data_type, native=True)


def serialize_msgpack(data: Any) -> bytes:
    return _MSGPACK_ENCODER.encode(data)


@overload
def deserialize_msgpack(data: bytes, data_type: None = None) -> Any: ...
@overload
def deserialize_msgpack[T](data: bytes, data_type: type[T]) -> T: ...
def deserialize_msgpack(data: bytes, data_type: Any = None) -> Any:
    return _decode(data, data_type, native=False)


if TYPE_CHECKING:
    # Deprecated module attributes, served by __getattr__ below; declared so type checkers keep resolving them
    enc_hook = _enc_hook
    ext_hook = _ext_hook
    MSGPACK_ENCODER = _MSGPACK_ENCODER
    MSGPACK_ENCODER_NATIVE = _MSGPACK_ENCODER_NATIVE
    MSGPACK_DECODER = _MSGPACK_DECODER
    MSGPACK_DECODER_NATIVE = _MSGPACK_DECODER_NATIVE

_DEPRECATED: dict[str, str] = {
    'enc_hook': '_enc_hook',
    'ext_hook': '_ext_hook',
    'MSGPACK_ENCODER': '_MSGPACK_ENCODER',
    'MSGPACK_ENCODER_NATIVE': '_MSGPACK_ENCODER_NATIVE',
    'MSGPACK_DECODER': '_MSGPACK_DECODER',
    'MSGPACK_DECODER_NATIVE': '_MSGPACK_DECODER_NATIVE',
}


def __getattr__(name: str) -> Any:
    """Serve the pre-0.29 public spellings of the internal hooks and codecs with a DeprecationWarning"""
    if (target := _DEPRECATED.get(name)) is None:
        msg = f'module {__name__!r} has no attribute {name!r}'

        raise AttributeError(msg)

    warnings.warn(
        f'python3_commons.serializers.msgspec.{name} is internal and deprecated; it is removed in 1.0. '
        'Use serialize_msgpack/deserialize_msgpack (or the _native variants) instead',
        DeprecationWarning,
        stacklevel=2,
    )

    return globals()[target]
