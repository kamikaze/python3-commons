# pyright: strict
import base64
import logging
import traceback
from datetime import UTC, datetime
from typing import Any, Final

import msgspec

__all__ = ['JSONFormatter']
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
        'message',
        'asctime',
    }
)
_RESERVED_KEYS: Final[frozenset[str]] = frozenset(
    {
        'message',
        'message_error',
        'args',
        'level',
        'logger',
        'timestamp',
        'exc_type',
        'exc_value',
        'exc_traceback',
        'stack',
        'extra',
    }
)


def _enc_hook(obj: Any) -> Any:
    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode('ascii')

    if isinstance(obj, Exception):
        return f'{type(obj).__name__}: {obj}'

    return repr(obj)


def _truncate(text: str, cap: int) -> str:
    if cap and len(text) > cap:
        return text[:cap] + '\n... <truncated>'

    return text


class JSONFormatter(logging.Formatter):
    """JSON log formatter.

    ``fmt`` and ``datefmt`` are accepted for ``logging.config.dictConfig`` compatibility and are ignored.
    Extra record attributes whose names collide with the reserved output keys are moved under ``extra``.
    """

    def __init__(
        self,
        fmt: str | None = None,  # noqa: ARG002  # dictConfig compatibility, ignored
        datefmt: str | None = None,  # noqa: ARG002  # dictConfig compatibility, ignored
        *,
        max_exc_tb_chars: int = _DEFAULT_MAX_TB_CHARS,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self._max_tb_chars = max_exc_tb_chars
        self._encoder = msgspec.json.Encoder(enc_hook=_enc_hook)

    def format(self, record: logging.LogRecord) -> str:
        cap = self._max_tb_chars
        message_error: str | None = None
        message_args: str | None = None

        try:
            message = record.getMessage()
        except Exception as e:  # noqa: BLE001  # a log call must never raise
            message = str(record.msg)
            message_args = repr(record.args)
            message_error = f'{type(e).__name__}: {e}'

        extras: dict[str, Any] = {}
        colliding: dict[str, Any] = {}

        for k, v in record.__dict__.items():
            if k[0] == '_' or k in _STD_LOG_FIELDS:
                continue

            if k in _RESERVED_KEYS:
                colliding[k] = v
            else:
                extras[k] = v

        log_dict = extras

        if colliding:
            log_dict['extra'] = colliding

        log_dict['message'] = message

        if message_error is not None:
            log_dict['message_error'] = message_error
            log_dict['args'] = message_args

        log_dict['level'] = record.levelname
        log_dict['logger'] = record.name
        log_dict['timestamp'] = datetime.fromtimestamp(record.created, UTC).isoformat().replace('+00:00', 'Z')

        if (exc_info := record.exc_info) and exc_info[0] is not None:
            exc_type, exc_value, exc_tb = exc_info

            log_dict['exc_type'] = f'{exc_type.__module__}.{exc_type.__qualname__}'
            log_dict['exc_value'] = str(exc_value)
            log_dict['exc_traceback'] = _truncate(
                ''.join(traceback.format_exception(exc_type, exc_value, exc_tb)).rstrip(), cap
            )

        if record.stack_info:
            log_dict['stack'] = _truncate(record.stack_info, cap)

        return self._encoder.encode(log_dict).decode('utf-8')
