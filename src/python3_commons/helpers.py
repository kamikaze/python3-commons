import logging
import shlex
import threading
from abc import ABCMeta
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from json import dumps
from typing import Literal, Mapping, Sequence
from urllib.parse import urlencode

from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


class SingletonMeta(ABCMeta):
    """
    A metaclass that creates a Singleton base class when called.
    """

    __instances = {}
    __locks = defaultdict(threading.Lock)

    def __call__(cls, *args, **kwargs):
        try:
            return cls.__instances[cls]
        except KeyError:
            with cls.__locks[cls]:
                try:
                    return cls.__instances[cls]
                except KeyError:
                    instance = super(SingletonMeta, cls).__call__(*args, **kwargs)
                    cls.__instances[cls] = instance

                    return instance


def date_from_string(string: str, fmt: str = '%d.%m.%Y') -> date:
    try:
        return datetime.strptime(string, fmt).date()
    except ValueError:
        return date.fromisoformat(string)


def datetime_from_string(string: str) -> datetime:
    try:
        return datetime.strptime(string, '%d.%m.%Y %H:%M:%S')
    except ValueError:
        return datetime.fromisoformat(string)


def date_range(start_date, end_date):
    for n in range(int((end_date - start_date).days + 1)):
        yield start_date + timedelta(days=n)


def tries(times):
    def func_wrapper(f):
        async def wrapper(*args, **kwargs):
            for time in range(times if times > 0 else 1):
                # noinspection PyBroadException
                try:
                    return await f(*args, **kwargs)
                except Exception as exc:
                    if time >= times:
                        raise exc

        return wrapper

    return func_wrapper


def round_decimal(value: Decimal, decimal_places=2, rounding_mode=ROUND_HALF_UP) -> Decimal:
    try:
        return value.quantize(Decimal(10) ** -decimal_places, rounding=rounding_mode)
    except AttributeError:
        return value


def request_to_curl(
    url: str,
    query: Mapping | None = None,
    method: Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete'] = 'get',
    headers: Mapping | None = None,
    json: Mapping | Sequence | str | None = None,
    data: bytes | None = None,
) -> str:
    if query:
        url = f'{url}?{urlencode(query)}'

    curl_cmd = ['curl', '-i', '-X', method.upper(), shlex.quote(url)]

    if headers:
        for key, value in headers.items():
            header_line = f'{key}: {value}'
            curl_cmd.append('-H')
            curl_cmd.append(shlex.quote(header_line))

    if json:
        curl_cmd.append('-H')
        curl_cmd.append(shlex.quote('Content-Type: application/json'))

        curl_cmd.append('-d')
        curl_cmd.append(shlex.quote(dumps(json, cls=CustomJSONEncoder)))
    elif data:
        curl_cmd.append('-d')
        curl_cmd.append(shlex.quote(data.decode('utf-8')))

    return ' '.join(curl_cmd)
