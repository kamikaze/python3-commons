import json
import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from logging import INFO, LogRecord
from typing import Any

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


def _format(formatter: JSONFormatter | None = None, **kwargs: Any) -> dict[str, Any]:
    """Format a record. ``msg``, ``args``, ``exc_info`` and ``stack_info`` set the record, other kwargs are extras."""
    record = logging.LogRecord(
        'n',
        INFO,
        '/dev/null',
        1,
        kwargs.pop('msg', 'm'),
        kwargs.pop('args', None),
        kwargs.pop('exc_info', None),
        sinfo=kwargs.pop('stack_info', None),
    )
    record.__dict__.update(kwargs)

    return json.loads((formatter or JSONFormatter()).format(record))


def test_stack_info_emitted_and_truncated() -> None:
    out = _format(JSONFormatter(max_exc_tb_chars=10), stack_info='S' * 50)

    assert out['stack'] == 'S' * 10 + '\n... <truncated>'
    assert 'stack_info' not in out


def test_exc_info_and_truncation() -> None:
    exc_info = (ValueError, ValueError('boom'), None)

    out = _format(JSONFormatter(max_exc_tb_chars=5), exc_info=exc_info)

    assert out['exc_type'] == 'builtins.ValueError'
    assert out['exc_value'] == 'boom'
    assert out['exc_traceback'].endswith('\n... <truncated>')


def test_reserved_keys_win_and_collisions_move_to_extra() -> None:
    out = _format(
        timestamp='FAKE', level='FAKE', logger='FAKE', exc_type='FAKE', message_error='FAKE', stack='FAKE', ok=1
    )

    assert out['level'] == 'INFO'
    assert out['logger'] == 'n'
    assert out['timestamp'] != 'FAKE'
    assert 'exc_type' not in out
    assert out['ok'] == 1
    assert out['extra'] == {
        'timestamp': 'FAKE',
        'level': 'FAKE',
        'logger': 'FAKE',
        'exc_type': 'FAKE',
        'message_error': 'FAKE',
        'stack': 'FAKE',
    }


def test_message_and_asctime_attributes_not_leaked() -> None:
    record = logging.LogRecord('n', INFO, '/dev/null', 1, 'm', None, None)
    logging.Formatter().format(record)  # sets record.message and record.asctime

    out = json.loads(JSONFormatter().format(record))

    assert 'asctime' not in out
    assert out['message'] == 'm'
    assert 'extra' not in out


def test_values_encoded_same_at_top_level_and_nested() -> None:
    out = _format(
        top=b'\xff\x00bin',
        nested={'b': b'\xff\x00bin', 'e': ValueError('boom')},
        err=ValueError('boom'),
        obj=object,
        dec=Decimal('1.5'),
        when=datetime(2024, 1, 2, 3, 4, 5),
    )

    assert out['top'] == out['nested']['b'] == '/wBiaW4='
    assert out['err'] == out['nested']['e'] == 'ValueError: boom'
    assert out['obj'] == repr(object)
    assert out['dec'] == '1.5'
    assert out['when'] == '2024-01-02T03:04:05'


def test_get_message_failure_reported() -> None:
    out = _format(msg='bad %d', args=('x',))

    assert out['message'] == 'bad %d'
    assert out['args'] == "('x',)"
    assert out['message_error'].startswith('TypeError')


def test_fmt_and_datefmt_accepted_and_ignored() -> None:
    out = _format(JSONFormatter('%(message)s!', '%Y', max_exc_tb_chars=5))

    assert out['message'] == 'm'
    assert JSONFormatter(fmt='x', datefmt='y')


def test_dictconfig_formatter_with_fmt(
    apply_dict_config: Callable[[dict[str, Any]], None], capsys: pytest.CaptureFixture[str]
) -> None:
    apply_dict_config(
        {
            'version': 1,
            'formatters': {'json': {'()': 'python3_commons.log.formatters.JSONFormatter', 'max_exc_tb_chars': 5}},
            'handlers': {'out': {'class': 'logging.StreamHandler', 'stream': 'ext://sys.stdout', 'formatter': 'json'}},
            'loggers': {'unit_log_fmt_app': {'handlers': ['out'], 'level': 'INFO', 'propagate': False}},
        }
    )
    logging.getLogger('unit_log_fmt_app').info('hi', extra={'k': 1})

    out = json.loads(capsys.readouterr().out)

    assert out['message'] == 'hi'
    assert out['k'] == 1


@pytest.mark.parametrize('cap', [0, 100])
def test_traceback_not_truncated_when_short_or_cap_disabled(cap: int) -> None:
    out = _format(JSONFormatter(max_exc_tb_chars=cap), exc_info=(ValueError, ValueError('boom'), None))

    assert out['exc_traceback'].startswith('ValueError: boom')
    assert 'truncated' not in out['exc_traceback']
