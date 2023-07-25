import json
import logging
import traceback

from python3_commons.serializers.json import CustomJSONEncoder


class JSONFormatter(logging.Formatter):
    @staticmethod
    def format_exception(exc_info):
        return ''.join(traceback.format_exception(*exc_info))

    def format(self, record):
        if record.exc_info:
            record.exc_text = self.format_exception(record.exc_info)
        else:
            record.exc_text = None

        return json.dumps(record.__dict__, cls=CustomJSONEncoder)
