import pytest
from pydantic import ValidationError

from python3_commons.conf import OIDCSettings


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
