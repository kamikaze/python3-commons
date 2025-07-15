from typing import Literal

from pydantic import Field, HttpUrl, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    logging_level: str = 'INFO'
    logging_format: str = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    logging_formatter: str = 'default'


class OIDCSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='OIDC_')

    enabled: bool = True
    authority_url: HttpUrl | None = None
    client_id: str | None = None


class ValkeySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='VALKEY_')

    dsn: RedisDsn | None = None
    sentinel_dsn: RedisDsn | None = None


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='DB_', validate_by_name=True, validate_by_alias=True)

    dsn: PostgresDsn | None = None
    scheme: str = 'postgresql+asyncpg'
    host: str = 'localhost'
    port: int = 5432
    name: str | None = None
    user: str | None = None
    password: SecretStr | None = Field(default=None, alias='DB_PASS')

    echo: bool = False
    pool_size: int = 20
    max_overflow: int = 0
    pool_timeout: int = 30
    pool_recycle: int = 1800  # 30 minutes

    @model_validator(mode='after')
    def build_dsn_if_missing(self) -> 'DBSettings':
        if self.dsn is None and all(
            (
                self.user,
                self.password,
                self.name,
            )
        ):
            self.dsn = PostgresDsn.build(
                scheme=self.scheme,
                username=self.user,
                password=self.password.get_secret_value() if self.password else None,
                host=self.host,
                port=self.port,
                path=self.name,
            )

        return self


class S3Settings(BaseSettings):
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: SecretStr = SecretStr('')
    s3_secret_access_key: SecretStr = SecretStr('')
    s3_addressing_style: Literal['path', 'virtual'] = 'virtual'
    s3_secure: bool = True
    s3_bucket: str | None = None
    s3_bucket_root: str | None = None
    s3_cert_verify: bool = True


settings = CommonSettings()
oidc_settings = OIDCSettings()
valkey_settings = ValkeySettings()
db_settings = DBSettings()
s3_settings = S3Settings()
