import logging
from collections.abc import Callable
from typing import Any

import pytest

from python3_commons.log.filters import MaxLevelFilter, filter_maker


def _record(level: int) -> logging.LogRecord:
    return logging.LogRecord('t', level, '/dev/null', 1, 'm', None, None)


def test_filter_maker_is_max_level_filter() -> None:
    assert filter_maker is MaxLevelFilter


@pytest.mark.parametrize('level', ['INFO', 'info', 'Info', 20])
def test_levels_accepted(level: int | str) -> None:
    f = MaxLevelFilter(level)

    assert f.level == logging.INFO
    assert f.filter(_record(logging.DEBUG))
    assert f.filter(_record(logging.INFO))
    assert not f.filter(_record(logging.WARNING))


def test_level_keyword() -> None:
    assert filter_maker(level='warning').level == logging.WARNING


@pytest.mark.parametrize('level', ['nope', 'Formatter', '', None, 1.5, True])
def test_invalid_level_raises(level: Any) -> None:
    with pytest.raises(ValueError, match='log level'):
        MaxLevelFilter(level)


@pytest.mark.parametrize('level', ['info', 20])
def test_dictconfig_template_entry(
    apply_dict_config: Callable[[dict[str, Any]], None], capsys: pytest.CaptureFixture[str], level: int | str
) -> None:
    apply_dict_config(
        {
            'version': 1,
            'formatters': {'json': {'()': 'python3_commons.log.formatters.JSONFormatter'}},
            'filters': {'info_and_below': {'()': 'python3_commons.log.filters.filter_maker', 'level': level}},
            'handlers': {
                'out': {
                    'class': 'logging.StreamHandler',
                    'stream': 'ext://sys.stdout',
                    'formatter': 'json',
                    'filters': ['info_and_below'],
                }
            },
            'loggers': {'unit_log_filter_app': {'handlers': ['out'], 'level': 'DEBUG', 'propagate': False}},
        }
    )
    logger = logging.getLogger('unit_log_filter_app')

    logger.info('shown')
    logger.warning('hidden')

    out = capsys.readouterr().out

    assert 'shown' in out
    assert 'hidden' not in out


def test_dictconfig_invalid_level_fails_at_configuration(apply_dict_config: Callable[[dict[str, Any]], None]) -> None:
    with pytest.raises(ValueError, match='filter'):
        apply_dict_config(
            {
                'version': 1,
                'filters': {'bad': {'()': 'python3_commons.log.filters.filter_maker', 'level': 'nope'}},
            }
        )
