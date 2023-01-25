import json
import logging

from python3_commons.json import CustomJSONEncoder


class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(record.__dict__, cls=CustomJSONEncoder)
