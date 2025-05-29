from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
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
class TestData:
    a: int
    b: str
    c: str | None
    d: datetime
    e: date
    f: Decimal


@pytest.fixture
def data_dataclass():
    return TestData(a=1, b='B', c=None, d=datetime(2023, 7, 25, 1, 2, 3), e=date(2023, 7, 24), f=Decimal('1.23'))


class SubStruc(msgspec.Struct):
    uid: UUID
    name: str


class TestStruct(msgspec.Struct):
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


class PydanticTestStruct(BaseModel):
    a: int
    b: str
    c: str | None
    d: datetime
    e: date
    f: Decimal
    sub: PydanticSubStruc


@pytest.fixture
def msgspec_struct() -> TestStruct:
    return TestStruct(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3),
        e=date(2023, 7, 24),
        f=Decimal('1.23'),
        sub=SubStruc(uid=uuid4(), name='sub-struct'),
    )


@pytest.fixture
def pydantic_struct() -> PydanticTestStruct:
    return PydanticTestStruct(
        a=1,
        b='B',
        c=None,
        d=datetime(2023, 7, 25, 1, 2, 3),
        e=date(2023, 7, 24),
        f=Decimal('1.23'),
        sub=PydanticSubStruc(uid=uuid4(), name='sub-struct'),
    )


@pytest.fixture
def s3_file_objects() -> tuple:
    return (
        (
            'file_a.txt',
            datetime(2024, 1, 1),
            b'ABCDE',
        ),
        (
            'file_b.txt',
            datetime(2024, 1, 2),
            b'FGHIJ',
        ),
        (
            'file_c.txt',
            datetime(2024, 1, 3),
            b'KLMNO',
        ),
        (
            'file_d.txt',
            datetime(2024, 1, 4),
            b'PQRST',
        ),
    )
