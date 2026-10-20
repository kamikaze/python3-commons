import logging
import traceback
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

import msgspec

_DEFAULT_MAX_TB_CHARS: Final[int] = 8_000
_STD_LOG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        'msg',
        'args',
        'levelname',
        'levelno',
        'pathname',
        'filename',
        'module',
        'exc_info',
        'exc_text',
        'stack_info',
        'lineno',
        'funcName',
        'created',
        'msecs',
        'relativeCreated',
        'taskName',
        'thread',
        'threadName',
        'process',
        'processName',
        'name',
    }
)


def _normalize(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()

    if isinstance(v, bytes):
        return v.decode('utf-8', errors='replace')

    if isinstance(v, Decimal | Exception):
        return str(v)

    return v


class JSONFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        max_exc_tb_chars: int = _DEFAULT_MAX_TB_CHARS,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self._max_tb_chars = max_exc_tb_chars
        self._encoder = msgspec.json.Encoder(enc_hook=repr)

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat().replace('+00:00', 'Z')
        log_dict: dict[str, Any] = {
            'message': message,
            'level': record.levelname,
            'logger': record.name,
            'timestamp': timestamp,
        }

        if (exc_info := record.exc_info) and exc_info[0] is not None:
            exc_type, exc_value, exc_tb = exc_info

            log_dict['exc_type'] = f'{exc_type.__module__}.{exc_type.__qualname__}'
            log_dict['exc_value'] = str(exc_value)

            tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
            cap = self._max_tb_chars

            if cap and len(tb) > cap:
                tb = tb[:cap] + '\n... <truncated>'

            log_dict['exc_traceback'] = tb

        log_dict.update(
            (k, _normalize(v)) for k, v in record.__dict__.items() if k[0] != '_' and k not in _STD_LOG_FIELDS
        )

        return self._encoder.encode(log_dict).decode('utf-8')
