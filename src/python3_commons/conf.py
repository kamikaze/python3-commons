from pydantic import Field, HttpUrl, PostgresDsn, RedisDsn, SecretStr
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
    model_config = SettingsConfigDict(env_prefix='DB_')

    dsn: PostgresDsn | None = Field(default=None, serialization_alias='url')
    echo: bool = False
    pool_size: int = 20
    max_overflow: int = 0
    pool_timeout: int = 30
    pool_recycle: int = 1800  # 30 minutes


class S3Settings(BaseSettings):
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_access_key_id: SecretStr = ''
    s3_secret_access_key: SecretStr = ''
    s3_secure: bool = True
    s3_bucket: str | None = None
    s3_bucket_root: str | None = None
    s3_cert_verify: bool = True


settings = CommonSettings()
oidc_settings = OIDCSettings()
valkey_settings = ValkeySettings()
db_settings = DBSettings()
s3_settings = S3Settings()
