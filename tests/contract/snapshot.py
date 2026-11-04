"""Checked-in expectations for the public API that downstream projects rely on.

Sources: fastapi-commons and fastapi-project-template. Each entry is the import path the consumer uses, not
where the name happens to be defined. A change to anything listed here is deliberate: update this file in the same
commit and add a CHANGELOG entry.
"""

# 'module:name' pairs, resolved with pkgutil.resolve_name
IMPORTED_NAMES: tuple[str, ...] = (
    'python3_commons.auth:OIDCClient',
    'python3_commons.auth:TokenData',
    'python3_commons.auth:OIDCProviderMetadata',
    'python3_commons.auth:JWKS',
    'python3_commons.auth:JWK',
    'python3_commons.conf:OIDCSettings',
    'python3_commons.conf:CommonSettings',
    'python3_commons.conf:DBSettings',
    'python3_commons.conf:StringSeq',
    'python3_commons.conf:get_common_settings',
    'python3_commons.conf:get_db_settings',
    'python3_commons.conf:get_valkey_settings',
    'python3_commons.conf:get_api_client_settings',
    'python3_commons.conf:get_object_storage_settings',
    'python3_commons.conf:get_oidc_settings',
    'python3_commons.db:AsyncSessionManager',
    'python3_commons.db:Base',
    'python3_commons.db:metadata',
    'python3_commons.db.models.common:BaseDBUUIDModel',
    'python3_commons.db.models.auth:ApiKey',
    # RBAC models used by python3_commons.permissions, re-exported by the template so Alembic sees them
    'python3_commons.db.models.rbac:RBACApiKeyRole',
    'python3_commons.db.models.rbac:RBACPermission',
    'python3_commons.db.models.rbac:RBACRole',
    'python3_commons.db.models.rbac:RBACRolePermission',
    'python3_commons.db.models.rbac:RBACUserRole',
    'python3_commons.db.models.users:User',
    'python3_commons.db.models.users:UserGroup',
    'python3_commons.exceptions:AppError',
)

# Names that moved out of helpers.py (3.D1); helpers still serves them, keeps them in __all__ and warns per name
HELPERS_MOVED_NAMES: frozenset[str] = frozenset(
    {
        'date_from_string',
        'date_range',
        'datetime_from_string',
        'log_execution_time',
        'parse_string_list',
        'replace_origin',
        'request_to_curl',
        'round_decimal',
        'to_snake_case',
    }
)

# Deprecated names a module keeps in __all__ (C2: a star import must keep binding them until 1.0), so its star
# import warns once per name. Every other deprecated name stays out of __all__.
STAR_IMPORT_DEPRECATED: dict[str, frozenset[str]] = {
    'python3_commons.helpers': HELPERS_MOVED_NAMES,
    'python3_commons.async_functools': frozenset({'LRUAsyncCallable'}),
}

# Names that still resolve but emit a DeprecationWarning naming 1.0 (removed in 5.7); the template imports db_settings
DEPRECATED_NAMES: tuple[str, ...] = (
    'python3_commons.conf:settings',
    'python3_commons.conf:db_settings',
    'python3_commons.conf:valkey_settings',
    'python3_commons.conf:S3Settings',
    'python3_commons.conf:get_s3_settings',
    'python3_commons.conf:s3_settings',
    'python3_commons.conf:api_client_settings',
    'python3_commons.conf:object_storage_settings',
    'python3_commons.api_client:s3_settings',
    'python3_commons.api_client:audit',
    *(f'python3_commons.helpers:{name}' for name in sorted(HELPERS_MOVED_NAMES)),
    'python3_commons.helpers:logger',
    *(
        f'python3_commons.serializers.msgspec:{name}'
        for name in (
            'enc_hook',
            'ext_hook',
            'MSGPACK_ENCODER',
            'MSGPACK_ENCODER_NATIVE',
            'MSGPACK_DECODER',
            'MSGPACK_DECODER_NATIVE',
        )
    ),
    'python3_commons.async_functools:LRUAsyncCallable',
    'python3_commons.async_functools:LRUAsyncBoundCallable',
    'python3_commons.async_functools:BaseLRUAsyncCallable',
    'python3_commons.async_functools:CachedLRUAsyncCallable',
    'python3_commons.async_functools:UncachedLRUAsyncCallable',
    'python3_commons.async_functools:AC',
    'python3_commons.async_functools:AC_invariant',
)

# dotted paths that logging.config.dictConfig resolves through the '()' key
DICTCONFIG_FACTORIES: tuple[str, ...] = (
    'python3_commons.log.formatters.JSONFormatter',
    'python3_commons.log.filters.filter_maker',
)

# str(inspect.signature(...)), as printed by Python 3.15
OIDC_CLIENT_INIT_SIGNATURE = (
    '(self, authority_url: pydantic.networks.HttpUrl, client_id: str, client_secret: str | None = None, *, '
    'timeout: float = 10.0, verify_cert: bool = True, connection_limit: int = 100, jwks_cache_ttl: float = 300.0, '
    'authority_internal_host: pydantic.networks.HttpUrl | None = None, audit_name: str | None = None, '
    'session: aiohttp.client.ClientSession | None = None, min_refresh_interval: float = 60.0) -> None'
)
OIDC_CLIENT_GET_JWKS_SIGNATURE = '(self, *, force_refresh: bool = False) -> dict[str, typing.Any]'
OIDC_CLIENT_FROM_SETTINGS_SIGNATURE = (
    '(settings: python3_commons.conf.OIDCSettings | None = None, **overrides: Any) -> Self'
)

# set(module.__all__) of every public module, except serializers/msgpack.py and db/helpers.py (A10: no __all__).
# Names served lazily by a module __getattr__ (see DEPRECATED_NAMES) stay out of the sets: a star-import must not warn.
PUBLIC_ALL: dict[str, frozenset[str]] = {
    'python3_commons': frozenset({'__version__'}),
    'python3_commons.api_client': frozenset(
        {
            'HttpVerb',
            'request',
        }
    ),
    'python3_commons.async_functools': frozenset(
        {
            'AsyncLRUCache',
            'BoundAsyncLRUCache',
            'CacheInfo',
            'CacheParameters',
            'LRUAsyncCallable',
            'async_lru_cache',
        }
    ),
    'python3_commons.audit': frozenset(
        {
            'normalize_audit_root',
            'store_audit',
            'write_audit_data',
            'write_audit_data_soon',
        }
    ),
    'python3_commons.auth': frozenset(
        {
            'DEFAULT_JWKS_CACHE_TTL',
            'DEFAULT_MIN_REFRESH_INTERVAL',
            'JWK',
            'JWKS',
            'OIDCAuthError',
            'OIDCClient',
            'OIDCError',
            'OIDCProviderMetadata',
            'OIDCTokenResponse',
            'TokenData',
        }
    ),
    'python3_commons.cache': frozenset(
        {
            'AsyncValkeyClient',
            'ValkeyCache',
            'add_set_item',
            'append_sequence',
            'close_client',
            'close_default_cache',
            'delete',
            'delete_dict_item',
            'delete_set_item',
            'exists',
            'get',
            'get_bytes',
            'get_default_cache',
            'get_dict',
            'get_dict_item',
            'get_sequence',
            'get_set_members',
            'get_string',
            'get_valkey_client',
            'has_set_item',
            'scan',
            'set_default_cache',
            'set_dict_item',
            'store',
            'store_bytes',
            'store_dict',
            'store_sequence',
            'store_set',
            'store_string',
            'update_dict',
        }
    ),
    'python3_commons.conf': frozenset(
        {
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
        }
    ),
    'python3_commons.db': frozenset(
        {
            'AsyncSessionManager',
            'Base',
            'DatabaseError',
            'EngineCreationError',
            'SessionAcquireError',
            'is_healthy',
            'metadata',
        }
    ),
    'python3_commons.db.models': frozenset(
        {
            'ApiKey',
            'RBACApiKeyRole',
            'RBACPermission',
            'RBACRole',
            'RBACRolePermission',
            'RBACUserRole',
            'User',
            'UserGroup',
            'load_models',
        }
    ),
    'python3_commons.db.models.auth': frozenset(
        {
            'ApiKey',
        }
    ),
    'python3_commons.db.models.common': frozenset(
        {
            'BaseDBModel',
            'BaseDBUUIDModel',
            'UTCNow',
        }
    ),
    'python3_commons.db.models.rbac': frozenset(
        {
            'RBACApiKeyRole',
            'RBACPermission',
            'RBACRole',
            'RBACRolePermission',
            'RBACUserRole',
        }
    ),
    'python3_commons.db.models.users': frozenset(
        {
            'User',
            'UserGroup',
        }
    ),
    'python3_commons.dates': frozenset(
        {
            'date_from_string',
            'date_range',
            'datetime_from_string',
        }
    ),
    'python3_commons.decimals': frozenset(
        {
            'round_decimal',
        }
    ),
    'python3_commons.decorators': frozenset(
        {
            'log_execution_time',
            'retry',
        }
    ),
    'python3_commons.exceptions': frozenset(
        {
            'AppError',
        }
    ),
    'python3_commons.fs': frozenset(
        {
            'iter_files',
        }
    ),
    'python3_commons.generators': frozenset(
        {
            'CsvFormatParams',
            'tuple_csv_stream',
        }
    ),
    'python3_commons.helpers': frozenset(
        {
            'SingletonMeta',
            'date_from_string',
            'date_range',
            'datetime_from_string',
            'log_execution_time',
            'parse_string_list',
            'replace_origin',
            'request_to_curl',
            'round_decimal',
            'to_snake_case',
            'tries',
        }
    ),
    'python3_commons.log': frozenset({}),
    'python3_commons.log.filters': frozenset(
        {
            'MaxLevelFilter',
            'filter_maker',
        }
    ),
    'python3_commons.log.formatters': frozenset(
        {
            'JSONFormatter',
        }
    ),
    'python3_commons.object_storage': frozenset(
        {
            'ObjectInfo',
            'ObjectKey',
            'ObjectStorage',
            'ObjectStorageStream',
            'build_url',
            'default_client',
            'get_client',
            'get_object',
            'get_object_stream',
            'get_object_streams',
            'get_objects',
            'list_objects',
            'normalize_root',
            'put_object',
            'put_object_stream',
            'remove_object',
            'remove_objects',
            'set_default_client',
        }
    ),
    'python3_commons.permissions': frozenset(
        {
            'has_api_key_permission',
            'has_user_permission',
        }
    ),
    'python3_commons.serializers': frozenset({}),
    'python3_commons.serializers.common': frozenset(
        {
            'ExtendedType',
        }
    ),
    'python3_commons.serializers.json': frozenset(
        {
            'CustomJSONEncoder',
            'dumps',
            'loads',
        }
    ),
    'python3_commons.serializers.msgspec': frozenset(
        {
            'deserialize_msgpack',
            'deserialize_msgpack_native',
            'serialize_msgpack',
            'serialize_msgpack_native',
        }
    ),
    'python3_commons.soap_client': frozenset(
        {
            'AsyncTransport',
            'TransportConfig',
            'abuild_soap_client',
            'build_soap_client',
            'soap_client',
        }
    ),
    'python3_commons.text': frozenset(
        {
            'parse_string_list',
            'slugify',
            'to_snake_case',
        }
    ),
    'python3_commons.urls': frozenset(
        {
            'replace_origin',
            'request_to_curl',
        }
    ),
    'python3_commons.zeep_audit': frozenset(
        {
            'ZeepAuditPlugin',
            'get_history_plugin',
        }
    ),
}
