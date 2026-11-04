import logging
import logging.config
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import msgspec
import pytest
from pydantic import BaseModel


@pytest.fixture
def data_dict():
    return {
        'A': 1,
        'B': 'B',
        'C': None,
        'D': datetime(2023, 7, 25, 1, 2, 3),
        'E': date(2023, 7, 24),
        'F': Decimal('1.23'),
    }


@dataclass
class SampleData:
    a: int
    b: str
    c: str | None
    d: datetime
    e: date
    f: Decimal


@pytest.fixture
def data_dataclass():
    return SampleData(a=1, b='B', c=None, d=datetime(2023, 7, 25, 1, 2, 3), e=date(2023, 7, 24), f=Decimal('1.23'))


class SubStruc(msgspec.Struct):
    uid: UUID
    name: str


class SampleStruct(msgspec.Struct):
    a: int
    b: str
    c: str | None
    d: datetime
    e: date
    f: Decimal
    sub: SubStruc


class PydanticSubStruc(BaseModel):
    uid: UUID
    name: str


class PydanticSampleStruct(BaseModel):
    a: int
    b: str
    c: str | None
    d: datetime
    e: date
    f: Decimal
    sub: PydanticSubStruc


@pytest.fixture
def msgspec_struct() -> SampleStruct:
    return SampleStruct(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3, tzinfo=UTC),
        e=date(2023, 7, 24),
        f=Decimal('1.23'),
        sub=SubStruc(uid=uuid4(), name='sub-struct'),
    )


@pytest.fixture
def pydantic_struct() -> PydanticSampleStruct:
    return PydanticSampleStruct(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3, tzinfo=UTC),
        e=date(2023, 7, 24),
        f=Decimal('1.23'),
        sub=PydanticSubStruc(uid=uuid4(), name='sub-struct'),
    )


@pytest.fixture
def apply_dict_config() -> Iterator[Callable[[dict[str, Any]], None]]:
    """Apply a ``logging.config.dictConfig`` dict and undo it afterwards.

    ``disable_existing_loggers`` is forced to False: True would silence every logger created before, in later tests.
    The root logger and the named loggers of the config get their handlers, level and propagation back.
    """
    touched: dict[str, tuple[list[logging.Handler], int, bool]] = {}

    def apply(config: dict[str, Any]) -> None:
        for name in ('', *config.get('loggers', {})):
            logger = logging.getLogger(name)
            touched.setdefault(name, (logger.handlers[:], logger.level, logger.propagate))

        logging.config.dictConfig({**config, 'disable_existing_loggers': False})

    yield apply

    for name, (handlers, level, propagate) in touched.items():
        logger = logging.getLogger(name)

        for handler in logger.handlers[:]:
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()

        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)

        logger.setLevel(level)
        logger.propagate = propagate
