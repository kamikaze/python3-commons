import logging
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime, date
from decimal import Decimal
from typing import Any, Final, Callable

import msgspec

correlation_id: ContextVar[str | None] = ContextVar('correlation_id', default=None)

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

    if isinstance(v, Decimal):
        return str(v)

    if isinstance(v, Exception):
        return str(v)

    return v


class JSONFormatter(logging.Formatter):
    __slots__ = ('_get_correlation_id', '_max_tb_chars', '_encoder')

    def __init__(
            self,
            *,
            get_correlation_id: Callable[[], str | None] = lambda: correlation_id.get(),
            max_exc_tb_chars: int = _DEFAULT_MAX_TB_CHARS,
            **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self._get_correlation_id = get_correlation_id
        self._max_tb_chars = max_exc_tb_chars
        self._encoder = msgspec.json.Encoder()

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat().replace('+00:00', 'Z')
        log: dict[str, Any] = {
            'message': message,
            'level': record.levelname,
            'logger': record.name,
            'timestamp': timestamp,
        }

        if (corr_id := self._get_correlation_id()) is not None:
            log['correlation_id'] = corr_id

        if (exc_info := record.exc_info) and exc_info[0] is not None:
            exc_type, exc_value, exc_tb = exc_info

            log['exc_type'] = f'{exc_type.__module__}.{exc_type.__qualname__}'
            log['exc_value'] = str(exc_value)

            tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip()
            cap = self._max_tb_chars

            if cap and len(tb) > cap:
                tb = tb[:cap] + '\n... <truncated>'

            log['exc_traceback'] = tb

        record_dict = record.__dict__
        std_log_fields = _STD_LOG_FIELDS

        if len(record_dict) > len(std_log_fields):
            normalize = _normalize
            out_set = log.__setitem__

            for k, v in record_dict.items():
                if k[0] == '_' or k in std_log_fields:
                    continue
                out_set(k, normalize(v))

        return self._encoder.encode(log).decode('utf-8')
