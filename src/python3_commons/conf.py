from pydantic import SecretStr
from pydantic_settings import BaseSettings


class CommonSettings(BaseSettings):
    logging_level: str = 'INFO'
    logging_format: str = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    logging_formatter: str = 'default'


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
s3_settings = S3Settings()
