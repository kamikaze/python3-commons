"""Smoke checks of what fastapi-project-template does with the library (ported from analysis/repro/downstream_smoke.py).

Copies of the template's code, not imports: python3-commons tests never import the template or fastapi-commons.
"""

import json
import logging
import subprocess
import sys
from collections.abc import Callable, Sequence
from typing import Annotated, Any

import msgspec
import pytest
from pydantic import HttpUrl
from pydantic_settings import NoDecode, SettingsConfigDict

from python3_commons.auth import OIDCClient, TokenData
from python3_commons.conf import CommonSettings, DBSettings, OIDCSettings, StringSeq

# What python3_commons.db.models.* must register in Base.metadata. A change to this set is deliberate.
LIBRARY_TABLES = frozenset(
    {
        'api_keys',
        'rbac_api_key_roles',
        'rbac_permissions',
        'rbac_role_permissions',
        'rbac_roles',
        'rbac_user_roles',
        'user_groups',
        'users',
    }
)


class AppTokenData(TokenData):
    """Shape of the template's AppTokenData (api/v1/auth.py:27-37): adds `groups`, overrides `roles`."""

    groups: Sequence[str] | None = None  # ty: ignore[invalid-attribute-override]  # pyright: ignore[reportIncompatibleVariableOverride]  # the template widens it

    @property
    def roles(self) -> list[str]:
        return sorted({*super().roles, *(self.groups or ())})


def test_app_token_data_converts_and_extends_roles() -> None:
    payload = {
        'exp': 2,
        'iat': 1,
        'iss': 'https://issuer.example',
        'sub': 'user-1',
        'groups': ['a'],
        'realm_access': {'roles': ['b']},
    }

    token = msgspec.convert(payload, type=AppTokenData)

    assert token.roles == ['a', 'b']


def test_ro_db_settings_reads_its_own_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template conf.py:37-38."""

    class RODBSettings(DBSettings):
        model_config = SettingsConfigDict(env_prefix='RO_DB_')

    monkeypatch.setenv('RO_DB_HOST', 'replica.example')
    monkeypatch.setenv('DB_HOST', 'primary.example')

    assert RODBSettings().host == 'replica.example'
    assert DBSettings().host == 'primary.example'


def test_settings_subclass_with_string_seq_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Template conf.py:13-18 and tests/unit/test_conf.py:11-13."""

    class Settings(CommonSettings):
        allowed_origins: Annotated[StringSeq, NoDecode] = ('*',)

    monkeypatch.setenv('ALLOWED_ORIGINS', 'http://a.example, http://b.example')

    assert Settings().allowed_origins == ('http://a.example', 'http://b.example')


def test_oidc_client_builds_from_oidc_settings_fields() -> None:
    """fastapi-commons auth.py:22-30 passes these OIDCSettings fields to OIDCClient."""
    oidc = OIDCSettings()

    client = OIDCClient(
        oidc.authority_url or HttpUrl('https://idp.example.com/'),
        oidc.client_id or 'cid',
        oidc.client_secret.get_secret_value(),
        timeout=oidc.timeout,
        verify_cert=oidc.verify_cert,
        connection_limit=oidc.connection_limit,
        authority_internal_host=oidc.authority_internal_host,
    )

    assert hasattr(client, '__dict__')  # instance attributes can be monkeypatched


def test_template_logging_config_applies(
    apply_dict_config: Callable[[dict[str, Any]], None], capsys: pytest.CaptureFixture[str]
) -> None:
    """Template conf.py:30-110 and jobs/__main__.py:9-60, without the fastapi_commons filters."""
    settings = CommonSettings()
    handlers = {
        'default_stdout': {
            'level': settings.logging_level,
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': 'json',
            'filters': ['info_and_below'],
        },
        'default_stderr': {
            'level': 'WARNING',
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stderr',
            'formatter': 'json',
        },
    }

    apply_dict_config(
        {
            'version': 1,
            'formatters': {
                'default': {'format': settings.logging_format},
                'json': {'()': 'python3_commons.log.formatters.JSONFormatter'},
            },
            'filters': {'info_and_below': {'()': 'python3_commons.log.filters.filter_maker', 'level': 'INFO'}},
            'handlers': handlers,
            'loggers': {
                '': {'handlers': ['default_stderr', 'default_stdout']},
                'contract_template_app': {
                    'handlers': ['default_stderr', 'default_stdout'],
                    'level': settings.logging_level,
                    'propagate': False,
                },
            },
        }
    )
    logger = logging.getLogger('contract_template_app')

    logger.info('hello', extra={'k': 1})
    logger.warning('careful')

    captured = capsys.readouterr()
    assert json.loads(captured.out.strip())['message'] == 'hello'
    assert json.loads(captured.err.strip())['message'] == 'careful'


def test_library_tables_are_registered_in_base_metadata() -> None:
    from python3_commons.db import Base  # noqa: PLC0415
    from python3_commons.db.models import load_models  # noqa: PLC0415

    assert load_models() is Base.metadata
    assert set(Base.metadata.tables) == LIBRARY_TABLES


def test_downstream_model_joins_the_library_metadata() -> None:
    """Template db/models/users.py: a downstream table shares Base.metadata with the library tables.

    Runs in a subprocess so the extra table does not stay in this process's metadata.
    """
    script = """
import json

import python3_commons.db.models.auth, python3_commons.db.models.rbac, python3_commons.db.models.users
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base
from python3_commons.db.models.common import BaseDBUUIDModel


class UserProfile(BaseDBUUIDModel, Base):
    __tablename__ = 'user_profiles'
    subject: Mapped[str] = mapped_column(String)


print(json.dumps(sorted(Base.metadata.tables)))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script], capture_output=True, text=True, timeout=60, check=True
    )

    assert set(json.loads(result.stdout)) == LIBRARY_TABLES | {'user_profiles'}


def test_db_settings_is_importable_from_conf() -> None:
    with pytest.warns(DeprecationWarning, match='1.0'):
        from python3_commons.conf import db_settings  # noqa: PLC0415

    assert isinstance(db_settings, DBSettings)
