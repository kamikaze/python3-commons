# pyright: strict
import functools
import logging
import warnings
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    HttpUrl,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from python3_commons import _paths
from python3_commons.text import parse_string_list

__all__ = [
    'ApiClientSettings',
    'CommonSettings',
    'DBSettings',
    'OIDCSettings',
    'ObjectStorageSettings',
    'StringSeq',
    'ValkeySettings',
    'get_api_client_settings',
    'get_common_settings',
    'get_db_settings',
    'get_object_storage_settings',
    'get_oidc_settings',
    'get_valkey_settings',
]
StringSeq = Annotated[Sequence[str], NoDecode, BeforeValidator(parse_string_list)]


def _parse_optional_string_list(value: str | Iterable[str] | None) -> tuple[str, ...] | None:
    return None if value is None else parse_string_list(value)


_MAX_PORT = 65535


def _split_host_port(entry: str, default_port: int = 26379) -> tuple[str, int]:
    """Split ``host``, ``host:port`` or ``[v6]:port``; raise ValueError on anything else."""
    host, sep, port = entry.strip().rpartition(':')

    if not sep or (host.endswith(':') and not host.endswith(']')):
        host, port = entry.strip(), str(default_port)

    host = host.removeprefix('[').removesuffix(']')

    if not host or not port.isdecimal() or not 0 < int(port) <= _MAX_PORT:
        msg = f'Invalid sentinel address {entry!r}, expected host:port'
        raise ValueError(msg)

    return host, int(port)


class CommonSettings(BaseSettings):
    logging_level: str = 'INFO'
    logging_format: str = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    logging_formatter: str = 'default'

    @field_validator('logging_level', mode='before')
    @classmethod
    def normalize_logging_level(cls, value: object) -> object:
        if isinstance(value, str):
            level = value.strip().upper()

            if level not in logging.getLevelNamesMapping():
                msg = f'Unknown logging level: {value!r}'
                raise ValueError(msg)

            return level

        return value


class OIDCSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='OIDC_')

    authority_url: HttpUrl | None = None  # https://login.externaldomain.com/realm/app
    authority_internal_host: HttpUrl | None = None  # http://localhost:8080/ which will substitute host in authority_url
    client_id: str | None = None
    client_secret: SecretStr = SecretStr('')
    redirect_uri: str | None = None
    scope: StringSeq = (
        'openid',
        'profile',
        'email',
    )
    audience: Annotated[Sequence[str] | None, NoDecode, BeforeValidator(_parse_optional_string_list)] = None
    issuer: str | None = None  # expected iss claim of tokens; not checked when empty
    leeway: int = Field(default=0, ge=0)  # allowed clock skew for exp and nbf of tokens, seconds
    jwks_refresh_interval: float = Field(default=60.0, ge=0)  # min seconds between JWKS refreshes on unknown kid
    jwks_cache_ttl: float = Field(default=300.0, ge=0)  # seconds JWKS keys are served from the cache
    verify_cert: bool = True
    timeout: float = Field(default=10, gt=0)
    connection_limit: int = Field(default=100, ge=0)


class ValkeySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='VALKEY_')

    dsn: RedisDsn | None = None
    sentinel_dsn: RedisDsn | None = None
    # further sentinel nodes as 'host:port' (port defaults to 26379); sentinel_dsn stays the first one
    sentinels: Annotated[Sequence[str] | None, NoDecode, BeforeValidator(_parse_optional_string_list)] = None
    sentinel_service_name: str = 'myprimary'
    sentinel_password: SecretStr | None = None  # password for the sentinel nodes; defaults to the one in sentinel_dsn

    # seconds
    socket_timeout: float = Field(default=60, gt=0)
    socket_connect_timeout: float = Field(default=10, gt=0)
    health_check_interval: int = Field(default=30, ge=0)  # seconds, 0 disables the check
    retries: int = Field(default=5, ge=0)  # retries on connection errors and timeouts, 0 disables retrying
    max_connections: int | None = Field(default=None, ge=1)  # None: unbounded pool; a number: blocking pool
    pool_timeout: float = Field(default=20, gt=0)  # seconds to wait for a free connection of a bounded pool

    @field_validator('sentinels')
    @classmethod
    def _check_sentinels(cls, value: Sequence[str] | None) -> Sequence[str] | None:
        for entry in value or ():
            _split_host_port(entry)

        return value

    @property
    def sentinel_nodes(self) -> list[tuple[str, int]]:
        """Addresses of all sentinel nodes: ``sentinel_dsn`` first, then ``sentinels``."""
        nodes = [(self.sentinel_dsn.host or 'localhost', self.sentinel_dsn.port or 26379)] if self.sentinel_dsn else []

        return nodes + [_split_host_port(entry) for entry in self.sentinels or ()]


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='DB_', env_prefix_target='all', validate_by_name=True, validate_by_alias=True
    )

    dsn: PostgresDsn | str | None = None
    scheme: str = 'postgresql+asyncpg'
    host: str = 'localhost'
    port: int = 5432
    name: str | None = None
    user: str | None = None
    password: SecretStr | None = Field(default=None, validation_alias=AliasChoices('pass', 'password', 'DB_PASS'))
    query: str | None = None

    echo: bool = False
    pool_size: int = Field(default=20, ge=0)
    max_overflow: int = Field(default=0, ge=-1)
    pool_timeout: int = Field(default=30, ge=0)
    pool_recycle: int = Field(default=1800, ge=-1)  # 30 minutes
    pool_pre_ping: bool = True
    statement_timeout: int = Field(default=30, ge=0)

    migration_role: str | None = None

    @model_validator(mode='after')
    def build_dsn_if_missing(self) -> DBSettings:
        if self.dsn is None and self.user and self.password and self.name:
            self.dsn = PostgresDsn.build(
                scheme=self.scheme,
                username=self.user,
                password=self.password.get_secret_value(),
                host=self.host,
                port=self.port,
                path=self.name,
                query=self.query,
            )

        return self


class _S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='S3_')

    access_key_id: SecretStr | None = None
    addressing_style: Literal['path', 'virtual'] = 'virtual'
    allow_http: bool = False
    region: str | None = None
    secret_access_key: SecretStr | None = None


_S3Settings.__name__ = _S3Settings.__qualname__ = 'S3Settings'


class ObjectStorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='OBJECT_STORAGE_')

    work_path: str | None = None

    @field_validator('work_path')
    @classmethod
    def _normalize_work_path(cls, v: str | None) -> str | None:
        """A blank root means "not configured"; any other root is stored as a canonical URL."""
        return _normalize_optional_root(v)


def _normalize_optional_root(v: str | None) -> str | None:
    """`None` for a missing or blank root, else `normalize_root(v)`: a URL, scheme-less roots as `file://`.

    A root `normalize_root` rejects (`/`, `s3://`, `file:///`) raises `ValueError`, which pydantic reports as a
    validation error when the settings are built.
    """
    return None if v is None or not v.strip() else _paths.normalize_root(v)


class ApiClientSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='API_CLIENT_')

    audit_path: str | None = None
    audit_timeout: float = Field(default=5.0, gt=0)
    audit_max_body_bytes: int = Field(default=1048576, ge=0)

    @field_validator('audit_path')
    @classmethod
    def _normalize_audit_path(cls, v: str | None) -> str | None:
        """A blank root means "audit off"; any other root is stored as a canonical URL."""
        return _normalize_optional_root(v)


@functools.cache
def get_common_settings() -> CommonSettings:
    return CommonSettings()


@functools.cache
def get_oidc_settings() -> OIDCSettings:
    """The shared OIDCSettings, still mutable. Additive: there is no legacy module attribute for it."""
    return OIDCSettings()


@functools.cache
def get_valkey_settings() -> ValkeySettings:
    return ValkeySettings()


@functools.cache
def get_db_settings() -> DBSettings:
    return DBSettings()


@functools.cache
def get_api_client_settings() -> ApiClientSettings:
    return ApiClientSettings()


@functools.cache
def _get_s3_settings() -> _S3Settings:
    return _S3Settings()


@functools.cache
def get_object_storage_settings() -> ObjectStorageSettings:
    return ObjectStorageSettings()


if TYPE_CHECKING:
    # Deprecated names, served lazily by __getattr__ below (with a warning); declared so type checkers keep them
    S3Settings = _S3Settings
    get_s3_settings = _get_s3_settings

    # Legacy module attributes, served lazily by __getattr__ below; declared so type checkers keep resolving them
    settings: CommonSettings
    db_settings: DBSettings
    valkey_settings: ValkeySettings
    s3_settings: _S3Settings
    api_client_settings: ApiClientSettings
    object_storage_settings: ObjectStorageSettings

# legacy attribute name -> name of the getter that replaces it: the attributes were module-level instances before 0.29.x
_LEGACY_SETTINGS: dict[str, str] = {
    'settings': 'get_common_settings',
    'db_settings': 'get_db_settings',
    'valkey_settings': 'get_valkey_settings',
    's3_settings': '_get_s3_settings',
    'api_client_settings': 'get_api_client_settings',
    'object_storage_settings': 'get_object_storage_settings',
}


# deprecated public name -> object served; S3Settings and get_s3_settings() go in 1.0 (F7)
_DEPRECATED_NAMES: dict[str, object] = {'S3Settings': _S3Settings, 'get_s3_settings': _get_s3_settings}


def __getattr__(name: str) -> object:
    """Serve the legacy module-level settings instances from the cached getters, with a DeprecationWarning."""
    if name in _DEPRECATED_NAMES:
        warnings.warn(
            f'python3_commons.conf.{name} is deprecated and will be removed in 1.0; python3-commons does not read '
            'S3 settings, object-storage-client reads S3_* and AWS_* from the environment itself',
            DeprecationWarning,
            stacklevel=2,
        )

        return _DEPRECATED_NAMES[name]

    try:
        getter_name = _LEGACY_SETTINGS[name]
    except KeyError:
        msg = f'module {__name__!r} has no attribute {name!r}'

        raise AttributeError(msg) from None

    warnings.warn(
        f'python3_commons.conf.{name} is deprecated and will be removed in 1.0; use {getter_name.lstrip("_")}()',
        DeprecationWarning,
        stacklevel=2,
    )

    return globals()[getter_name]()
