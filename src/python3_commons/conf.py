from pydantic import SecretStr, PostgresDsn
from pydantic_settings import BaseSettings


class CommonSettings(BaseSettings):
    logging_level: str = 'INFO'
    logging_format: str = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    logging_formatter: str = 'default'


class DBSettings(BaseSettings):
    dsn: PostgresDsn | None = None
    echo: bool = False,
    pool_size: int = 20,
    max_overflow: int = 0,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,  # 30 minutes

    class Config:
        env_prefix = 'DB_'


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
db_settings = DBSettings()
s3_settings = S3Settings()
