import base64
import dataclasses
import json
from datetime import datetime, date
from decimal import Decimal
from socket import socket
from typing import Any


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o) -> Any:
        try:
            return super(CustomJSONEncoder, self).default(o)
        except TypeError:
            if isinstance(o, datetime):
                return o.isoformat()
            elif isinstance(o, date):
                return o.isoformat()
            elif isinstance(o, bytes):
                return base64.b64encode(o).decode('ascii')
            elif dataclasses.is_dataclass(o):
                return dataclasses.asdict(o)
            elif isinstance(o, (Decimal, socket, type, Exception)):
                return str(o)

        return type(o).__name__
