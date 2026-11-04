import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from python3_commons.conf import CommonSettings, DBSettings, OIDCSettings, ValkeySettings


def test_oidc_settings_token_checks_defaults(monkeypatch):
    for name in ('OIDC_ISSUER', 'OIDC_LEEWAY', 'OIDC_JWKS_REFRESH_INTERVAL'):
        monkeypatch.delenv(name, raising=False)
    settings = OIDCSettings()

    assert settings.issuer is None
    assert settings.leeway == 0
    assert settings.jwks_refresh_interval == 60.0


def test_oidc_settings_token_checks_from_env(monkeypatch):
    monkeypatch.setenv('OIDC_ISSUER', 'https://issuer.example')
    monkeypatch.setenv('OIDC_LEEWAY', '30')
    monkeypatch.setenv('OIDC_JWKS_REFRESH_INTERVAL', '5')
    settings = OIDCSettings()

    assert settings.issuer == 'https://issuer.example'
    assert settings.leeway == 30
    assert settings.jwks_refresh_interval == 5.0


@pytest.mark.parametrize('field', ['leeway', 'jwks_refresh_interval'])
def test_oidc_settings_token_checks_not_negative(field):
    with pytest.raises(ValidationError):
        OIDCSettings.model_validate({field: -1})


@pytest.mark.parametrize('field', ['scope', 'audience'])
@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('openid,profile,email', ('openid', 'profile', 'email')),
        ('openid profile', ('openid profile',)),
        (' a , b ', ('a', 'b')),
        ('a,b,', ('a', 'b')),
        ('', ()),
        ('single', ('single',)),
        ('["a", "b"]', ('a', 'b')),
    ],
)
def test_oidc_string_sequences_from_env(monkeypatch, field, raw, expected):
    monkeypatch.setenv(f'OIDC_{field.upper()}', raw)

    assert getattr(OIDCSettings(), field) == expected


@pytest.mark.parametrize(
    'kwargs',
    [{'timeout': 0}, {'connection_limit': -1}],
)
def test_oidc_settings_bounds(kwargs):
    with pytest.raises(ValidationError):
        OIDCSettings.model_validate(kwargs)


@pytest.mark.parametrize(
    'kwargs',
    [
        {'pool_size': -1},
        {'max_overflow': -2},
        {'pool_recycle': -2},
        {'pool_timeout': -1},
        {'statement_timeout': -1},
    ],
)
def test_db_settings_bounds(kwargs):
    with pytest.raises(ValidationError):
        DBSettings.model_validate(kwargs)


def test_db_settings_bounds_allow_edges():
    db = DBSettings.model_validate({'pool_size': 0, 'max_overflow': -1, 'pool_recycle': -1, 'pool_timeout': 0})

    assert (db.pool_size, db.max_overflow, db.pool_recycle, db.pool_timeout) == (0, -1, -1, 0)


class RODBSettings(DBSettings):
    model_config = SettingsConfigDict(env_prefix='RO_DB_')


def _password(settings: DBSettings) -> str:
    assert settings.password is not None

    return settings.password.get_secret_value()


def test_db_password_aliases(monkeypatch):
    monkeypatch.delenv('DB_PASS', raising=False)
    monkeypatch.delenv('DB_PASSWORD', raising=False)

    assert _password(DBSettings(password=SecretStr('x'))) == 'x'
    assert _password(DBSettings.model_validate({'pass': 'y'})) == 'y'
    monkeypatch.setenv('DB_PASS', 'z')

    assert _password(DBSettings()) == 'z'


def test_ro_db_settings_read_own_prefix_and_do_not_inherit_db_pass(monkeypatch):
    monkeypatch.setenv('DB_PASS', 'main')
    monkeypatch.setenv('RO_DB_PASS', 'ro')

    assert _password(RODBSettings()) == 'ro'

    monkeypatch.delenv('RO_DB_PASS')

    assert RODBSettings().password is None
    assert _password(DBSettings()) == 'main'


def test_logging_level_normalised(monkeypatch):
    monkeypatch.setenv('LOGGING_LEVEL', 'debug')

    assert CommonSettings().logging_level == 'DEBUG'

    monkeypatch.setenv('LOGGING_LEVEL', 'nonsense')

    with pytest.raises(ValidationError):
        CommonSettings()


def test_logging_level_non_string_is_left_to_field_validation():
    with pytest.raises(ValidationError):
        CommonSettings(logging_level=10)  # pyright: ignore[reportArgumentType]


def test_db_dsn_built_from_parts_and_password_field_masked():
    # The built dsn carries the password in clear (decision S2: left as is); only the password field is masked
    settings = DBSettings(user='u', password=SecretStr('s3cret'), name='db', host='h', port=5433)

    assert settings.dsn is not None
    assert str(settings.dsn).startswith('postgresql+asyncpg://u:s3cret@h:5433/db')
    assert settings.password is not None
    assert 's3cret' not in str(settings.password)
    assert "password=SecretStr('**********')" in repr(settings)


def test_db_dsn_not_built_when_parts_are_missing():
    assert DBSettings(user='u', password=SecretStr('p')).dsn is None


def test_explicit_dsn_is_kept():
    settings = DBSettings(dsn='postgresql+asyncpg://a:b@c/d', user='u', password=SecretStr('p'), name='n')  # pyright: ignore[reportArgumentType]

    assert str(settings.dsn) == 'postgresql+asyncpg://a:b@c/d'


def test_valkey_sentinel_password_stays_out_of_reprs():
    settings = ValkeySettings(sentinel_password=SecretStr('hunter2'))

    assert 'hunter2' not in repr(settings)
    assert 'hunter2' not in settings.model_dump_json()
