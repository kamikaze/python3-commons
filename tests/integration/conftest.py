from os import getenv

import pytest
from pydantic import HttpUrl


@pytest.fixture(scope='session')
def authority_url() -> HttpUrl:
    return HttpUrl(getenv('TEST_OIDC_AUTHORITY_URL', ''))


@pytest.fixture(scope='session')
def client_id() -> str:
    return getenv('TEST_OIDC_CLIENT_ID', '')


@pytest.fixture(scope='session')
def client_secret() -> str:
    return getenv('TEST_OIDC_CLIENT_SECRET', '')


@pytest.fixture(scope='session')
def oidc_username() -> str:
    return getenv('TEST_OIDC_USERNAME', '')


@pytest.fixture(scope='session')
def oidc_password() -> str:
    return getenv('TEST_OIDC_PASSWORD', '')
