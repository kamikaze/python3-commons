import json
from logging import INFO, LogRecord

import pytest

from python3_commons.log.formatters import JSONFormatter


def test_json_formatter_info_record() -> None:
    formatter = JSONFormatter()
    record = LogRecord(
        name='test.log',
        level=INFO,
        pathname='/dev/null',
        lineno=12345,
        msg='This is a %s, %d, %s',
        args=(
            'test',
            1234,
            'magic',
        ),
        exc_info=None,
    )
    expected_message = 'This is a test, 1234, magic'
    formatted_record_str = formatter.format(record)
    formatted_record = json.loads(formatted_record_str)

    with pytest.raises(KeyError):
        _ = formatted_record['msg']

    with pytest.raises(KeyError):
        _ = formatted_record['args']

    assert formatted_record['message'] == expected_message
