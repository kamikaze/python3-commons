from collections.abc import AsyncGenerator, Sequence

import pytest

from python3_commons.generators import tuple_csv_stream


async def _rows(rows: Sequence[Sequence[object]]) -> AsyncGenerator[Sequence[object]]:
    for row in rows:
        yield row


async def _collect(stream: AsyncGenerator[bytes]) -> bytes:
    return b''.join([chunk async for chunk in stream])


@pytest.mark.asyncio
async def test_default_dialect_output_unchanged():
    out = await _collect(tuple_csv_stream(_rows([('a', 'b c'), (1, 2)]), header=('h1', 'h2')))

    assert out == b'h1,h2\r\na,b c\r\n1,2\r\n'


@pytest.mark.asyncio
async def test_excel_tab_uses_tabs():
    out = await _collect(tuple_csv_stream(_rows([('a', 'b')]), ('h1', 'h2'), 'excel-tab'))

    assert out == b'h1\th2\r\na\tb\r\n'


@pytest.mark.asyncio
async def test_unix_dialect_uses_lf_and_quotes_all():
    out = await _collect(tuple_csv_stream(_rows([('a', 1)]), dialect='unix'))

    assert out == b'"a","1"\n'


@pytest.mark.asyncio
async def test_explicit_delimiter_overrides_dialect():
    out = await _collect(tuple_csv_stream(_rows([('a', 'b')]), dialect='excel-tab', delimiter=';'))

    assert out == b'a;b\r\n'


@pytest.mark.asyncio
async def test_accepts_plain_async_iterable_without_aclose():
    class Source:
        def __aiter__(self):
            return self._gen()

        @staticmethod
        async def _gen():
            yield ('x', 'y')

    out = await _collect(tuple_csv_stream(Source()))

    assert out == b'x,y\r\n'


@pytest.mark.asyncio
async def test_early_aclose_runs_source_finally_synchronously():
    log: list[str] = []

    async def source() -> AsyncGenerator[tuple[int, int]]:
        try:
            for i in range(10):
                yield (i, i)
        finally:
            log.append('closed')

    stream = tuple_csv_stream(source())

    assert await anext(stream) == b'0,0\r\n'
    assert await anext(stream) == b'1,1\r\n'

    await stream.aclose()

    assert log == ['closed']
