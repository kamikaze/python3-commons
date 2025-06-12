import pytest

from python3_commons import cache


@pytest.mark.asyncio
async def test_encode_decode_dict_to_msgpack(msgspec_struct):
    await cache.store('test:key', msgspec_struct, 8 * 3600)
