"""Opt-in test against a real OIDC provider (Cognito): skipped unless TEST_OIDC_AUTHORITY_URL is set, also under
REQUIRE_CONTAINERS=1 (it needs no container). The hermetic OIDC client tests are tests/unit/test_auth_idp.py.
"""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pydantic import HttpUrl

from python3_commons.auth import OIDCClient


@pytest.mark.asyncio
async def test_get_token_cognito(
    authority_url: HttpUrl, client_id: str, client_secret: str, oidc_username: str, oidc_password: str
) -> None:
    async with (
        OIDCClient(
            authority_url=authority_url,
            client_id=client_id,
            client_secret=client_secret,
            timeout=10,
        ) as client,
    ):
        token = await client.fetch_token(
            username=oidc_username,
            password=oidc_password,
        )

        assert token.access_token
