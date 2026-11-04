import base64
import dataclasses
import json
import os
import warnings
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from socket import socket
from typing import Any
from uuid import UUID

import msgspec.json
from pydantic import BaseModel

__all__ = [
    'CustomJSONEncoder',
    'dumps',
    'loads',
]
_STRINGIFY_WARNING = (
    'CustomJSONEncoder stringifying a {kind} is deprecated and will raise TypeError in python3-commons 1.0'
)


# The encoder is called from the json stdlib (C or pure-Python path, at varying depths) and from library code such as
# api_client, so the warning is attributed to the first frame outside both.
_SKIP_FILE_PREFIXES = (
    f'{Path(json.__file__).parent}{os.sep}',
    f'{Path(__file__).resolve().parent.parent}{os.sep}',
)


def _warn_stringified(kind: str) -> None:
    warnings.warn(_STRINGIFY_WARNING.format(kind=kind), DeprecationWarning, skip_file_prefixes=_SKIP_FILE_PREFIXES)


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:  # noqa: C901, PLR0911  # one branch per supported type
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, bytes):
            return base64.b64encode(o).decode('ascii')
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, (set, frozenset)):
            return list(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, BaseModel):
            return o.model_dump(mode='json')
        if isinstance(o, type):
            _warn_stringified('type')

            return str(o)
        if isinstance(o, socket):
            _warn_stringified('socket')

            return str(o)
        if isinstance(o, (Decimal, Exception)):
            return str(o)

        return super().default(o)


def _enc_hook(obj: Any) -> Any:
    # msgspec handles UUID, set, Enum, dataclasses, bytes, Decimal and datetime natively
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode='json')
    if isinstance(obj, Exception):
        return str(obj)

    msg = f'Objects of type {type(obj)} are not supported'

    raise TypeError(msg)


_encoder = msgspec.json.Encoder(enc_hook=_enc_hook)


def dumps(obj: Any) -> bytes:
    """Encode to JSON bytes with msgspec. Not wire-compatible with CustomJSONEncoder (UTC datetimes use `Z`)."""
    return _encoder.encode(obj)


def loads(data: bytes | str) -> Any:
    """Decode JSON to plain Python values (UUIDs, enums and sets come back as str/list)."""
    return msgspec.json.decode(data)
