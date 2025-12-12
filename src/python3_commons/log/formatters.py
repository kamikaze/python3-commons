import json
import logging
import traceback
from contextvars import ContextVar

from python3_commons.serializers.json import CustomJSONEncoder

correlation_id: ContextVar[str | None] = ContextVar('correlation_id', default=None)


class JSONFormatter(logging.Formatter):
    @staticmethod
    def format_exception(exc_info: logging._SysExcInfoType) -> str:
        return ''.join(traceback.format_exception(*exc_info))

    def format(self, record: logging.LogRecord) -> str:
        if corr_id := correlation_id.get():
            record.correlation_id = corr_id

        record.message = record.getMessage()

        if record.exc_info:
            record.exc_text = self.format_exception(record.exc_info)
        else:
            record.exc_text = None

        record_dict = record.__dict__

        del record_dict['args']
        del record_dict['msg']

        return json.dumps(record_dict, cls=CustomJSONEncoder)
