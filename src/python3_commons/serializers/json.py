import base64
import dataclasses
import json
from datetime import date, datetime
from decimal import Decimal
from socket import socket
from typing import Any


class CustomJSONEncoder(json.JSONEncoder):
    def default(self, o) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, bytes):
            return base64.b64encode(o).decode('ascii')
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (Decimal, socket, type, Exception)):
            return str(o)

        return super().default(o)
