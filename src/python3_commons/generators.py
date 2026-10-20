import csv
import io
from collections.abc import AsyncGenerator
from csv import Dialect
from typing import Literal


async def tuple_csv_stream(
    generator: AsyncGenerator[tuple],
    header: tuple | None = None,
    dialect: Dialect = 'excel',
    *,
    delimiter: str = ',',
    quotechar: str | None = '"',
    escapechar: str | None = None,
    doublequote: bool = True,
    skipinitialspace: bool = False,
    lineterminator: str = '\r\n',
    quoting: Literal[0, 1, 2, 3, 4, 5] = csv.QUOTE_MINIMAL,
    strict: bool = False,
) -> AsyncGenerator[bytes]:
    buffer = io.StringIO()
    writer = csv.writer(
        buffer,
        dialect=dialect,
        delimiter=delimiter,
        quotechar=quotechar,
        escapechar=escapechar,
        doublequote=doublequote,
        skipinitialspace=skipinitialspace,
        lineterminator=lineterminator,
        quoting=quoting,
        strict=strict,
    )

    def drain() -> bytes:
        data = buffer.getvalue().encode('utf-8')
        buffer.seek(0)
        buffer.truncate(0)

        return data

    if header:
        writer.writerow(header)
        yield drain()

    async for row in generator:
        writer.writerow(row)
        yield drain()
