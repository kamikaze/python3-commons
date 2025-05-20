import json
import logging
import traceback
from contextvars import ContextVar

from python3_commons.serializers.json import CustomJSONEncoder

correlation_id: ContextVar[str | None] = ContextVar('correlation_id', default=None)


class JSONFormatter(logging.Formatter):
    @staticmethod
    def format_exception(exc_info):
        return ''.join(traceback.format_exception(*exc_info))

    def format(self, record):
        if corr_id := correlation_id.get():
            record.correlation_id = corr_id

        if record.exc_info:
            record.exc_text = self.format_exception(record.exc_info)
        else:
            record.exc_text = None

        return json.dumps(record.__dict__, cls=CustomJSONEncoder)
