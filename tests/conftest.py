from dataclasses import dataclass
from datetime import datetime, date
from typing import Any

import msgspec
import pytest


@pytest.fixture
def data_dict():
    return {
        'A': 1,
        'B': 'B',
        'C': None,
        'D': datetime(2023, 7, 25, 1, 2, 3),
        'E': date(2023, 7, 24),
    }


@dataclass
class TestData:
    a: int
    b: str
    c: str | None
    d: datetime
    e: date


@pytest.fixture
def data_dataclass():
    return TestData(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3),
        e=date(2023, 7, 24),
    )


class TestStruct(msgspec.Struct):
    a: int
    b: str
    c: str | None
    d: datetime
    e: date


@pytest.fixture
def data_struct():
    return TestStruct(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3),
        e=date(2023, 7, 24),
    )
