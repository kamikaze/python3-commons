# pyright: strict
import contextlib
import csv
import io
from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from typing import Literal, TypedDict, Unpack, cast

__all__ = ('CsvFormatParams', 'tuple_csv_stream')


class CsvFormatParams(TypedDict, total=False):
    """Optional csv.writer format parameters; they override the chosen dialect."""

    delimiter: str
    quotechar: str | None
    escapechar: str | None
    doublequote: bool
    skipinitialspace: bool
    lineterminator: str
    quoting: Literal[0, 1, 2, 3, 4, 5]
    strict: bool


async def tuple_csv_stream(
    generator: AsyncIterable[Sequence[object]],
    header: Sequence[object] | None = None,
    dialect: str | type[csv.Dialect] = 'excel',
    **fmtparams: Unpack[CsvFormatParams],
) -> AsyncGenerator[bytes]:
    """Stream rows from an async iterable as UTF-8 encoded CSV chunks, one per row.

    Only the format parameters the caller supplies override the dialect. If the source
    has ``aclose`` it is closed when this stream finishes or is closed early.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect, **fmtparams)

    def drain() -> bytes:
        data = buffer.getvalue().encode('utf-8')
        buffer.seek(0)
        buffer.truncate(0)

        return data

    if header:
        writer.writerow(header)

        yield drain()

    guard: contextlib.AbstractAsyncContextManager[object] = (
        contextlib.aclosing(cast('AsyncGenerator[Sequence[object]]', generator))
        if hasattr(generator, 'aclose')
        else contextlib.nullcontext()
    )

    async with guard:
        async for row in generator:
            writer.writerow(row)

            yield drain()
