# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **Action required:** audit is now written only under `API_CLIENT_AUDIT_PATH`, a full object storage path such as
  `s3://bucket/audit`. The fallbacks are gone, with no deprecation period: audit no longer turns on when only
  `OBJECT_STORAGE_WORK_PATH` is set (it was written under `<work_path>/audit/`) or when only `S3_SECRET_ACCESS_KEY`
  is set (root `audit`). Deployments that audit through either fallback must set `API_CLIENT_AUDIT_PATH` before
  upgrading; without it, audit is disabled. `OBJECT_STORAGE_WORK_PATH` stays the setting for general object-storage
  work.
- **Action required:** a storage root without a scheme (`OBJECT_STORAGE_WORK_PATH`, `API_CLIENT_AUDIT_PATH`,
  `ObjectStorage(root)`, the `work_path` of the module functions) is now a local filesystem path, normalised to a
  `file://` URL. Relative paths resolve against the working directory and `~` expands. Before this change such a
  root, `bucket` or `bucket:prefix`, meant `s3://bucket/...`. Set an explicit `s3://bucket/prefix`. The `bucket:prefix`
  form is no longer rewritten.
- **Action required:** the `object-storage` extra requires `object-storage-client>=0.4.1,<0.5` (was `>=0.3.0,<0.4`).
  In 0.4 every client method starts its operation when called and returns an `asyncio.Future` instead of a
  coroutine. python3-commons only awaits them, so its functions behave as before. Code that calls the client returned
  by `object_storage.default_client()`, `get_client()` or `ObjectStorage.client` directly can still await the
  calls or pass them to `asyncio.gather`, but `asyncio.create_task` and `TaskGroup.create_task` reject them: wrap the
  call in a coroutine. A call made without a running event loop raises `RuntimeError`.
- Audit object keys no longer contain empty path segments (`a//b` is stored as `a/b`), and `api_client` omits the uri
  segment for a request to `base_url` without a uri (no more `name//post_...` keys).
- `python3_commons.audit` and `python3_commons.api_client` no longer import `zeep` and `lxml`. `ZeepAuditPlugin` and
  `get_history_plugin` live in `python3_commons.zeep_audit`; the old names stay importable from
  `python3_commons.audit` and are loaded on access. The extras are unchanged: `api-client` still requires `audit`.
- Tests: pytest configuration is strict, warnings are errors and coverage settings moved into `pyproject.toml`
  (`.coveragerc` removed); the coverage floor (`fail_under`) is 75%. The `build`, `setuptools`, `setuptools_scm` and `wheel` packages left the `dev`
  dependency group, and `uv` syncs the `dev` and `testing` groups by default.
- CI: checks run on pushes and pull requests, and can be called from other workflows. Lint installs only the `dev`
  group, and a new blocking `typecheck` job runs `ty` (warnings are errors) and `pyright` on `src` and `tests`, on
  `tests/contract/typing_consumer.py`, and a `pyright --verifytypes` completeness ratchet against
  `tests/contract/type_completeness.json`. The `tests` job runs on Python 3.14.10 and 3.15 (each leg tests its own
  interpreter, not the one `.python-version` pins) and also runs `tests/contract`, which pins the public API that
  downstream projects use and that importing a module loads no heavy optional package (from `auth`, `api_client`),
  builds no settings, log handler or object-storage client, and leaves the ORM metadata FK-complete.
- Release: one workflow, `release-on-tag-push.yml`, runs on `v*` tags. It runs the checks, builds with `uv build`,
  publishes to PyPI by Trusted Publishing (environment `pypi`, no API token), then creates the GitHub release with
  notes linking CHANGELOG.md (and MIGRATION.md when the tagged tree has it). `python-publish.yaml` is removed, so a
  release is published once. Every action is pinned by commit SHA, and the workflows run with a read-only token.
- Packaging: the build requires `setuptools>=80` and `setuptools-scm>=8` (`wheel` is no longer needed), the obsolete
  `bdist_wheel` universal flag is gone (the wheel stays `py3-none-any`), and the sdist leaves out `.github` and
  `.env_template` but keeps the tests. The release build fails when the built version differs from the tag.
- `AsyncSessionManager`: `dispose_all()` is an alias of the new `aclose()`; `get_async_session(name)` returns the same
  dependency callable per name; the asyncpg-only `connect_args` (`timeout`, `command_timeout`) are applied only to
  asyncpg DSNs; an explicitly set `DB_POOL_TIMEOUT` above `pool_acquire_timeout` logs a WARNING (the cap stays); a
  missing or empty DSN raises `EngineCreationError('DSN not configured for ...')` before an engine is built; errors it
  re-raises are no longer also logged, and `is_healthy` logs one WARNING without a traceback. The `engines` and
  `session_makers` attributes are read-only (assigning to them raises `AttributeError`).
- `serializers.msgspec`: pydantic models are encoded at any depth (both variants), and typed decoding accepts generic
  targets such as `list[Struct]`, `dict[str, Model]` and `Model | None`, with cached decoders. The dead Decimal,
  datetime, date and dataclass encode branches are gone; the encoded bytes are unchanged (msgspec encodes those types
  natively), `ext_hook` still decodes legacy extension data, and untyped decoding is unchanged (Decimal and datetime
  come back as `str`, a set as `list`).
- Package metadata: `Typing :: Typed`, `Programming Language :: Python :: 3 :: Only`, `3.14`, `3.15`,
  `Framework :: AsyncIO`, `Intended Audience :: Developers` and `Operating System :: OS Independent` classifiers,
  keywords, and Source, Issues and Changelog project URLs.
- `async_functools.async_lru_cache` returns a generic `AsyncLRUCache[**P, R]`, so type checkers see the wrapped
  function's parameters and result; a cached method gives `BoundAsyncLRUCache[Q, R]` (its `__call__` is now
  `async def`). The overloads are `(fn, /)` and `(maxsize=128, *, typed=False)`. Runtime behaviour is unchanged;
  `functools._make_key` is vendored, and the docstring notes that a cache on a method keeps its instances alive.
- `python3_commons.__version__` is resolved on first access, so importing the package no longer imports
  `importlib.metadata` (about 3.5 ms instead of 17 ms). The test configuration drops `pythonpath = ["src"]`: tests
  use the installed editable package (refresh a stale copy with `uv sync --reinstall-package python3-commons`).
- Lint: ruff also checks ANN (ANN401 ignored), ARG, BLE, C90, DTZ and PLR; the A10 modules are exempt by per-file
  ignores. A few signatures gained annotations (`cache.get_dict`, `cache.delete_dict_item`, `round_decimal`,
  `get_session_context`, `CustomJSONEncoder.default`); no behaviour change.
- Typing: bare generics in public signatures are parameterised (`Mapping[Any, Any]`, `Sequence[Any]`,
  `dict[Any, Any]`, `set[Any]`), with the same meaning for type checkers. pyright runs in standard mode with
  `reportMissingParameterType`, `reportMissingTypeArgument`, `reportPrivateUsage` and `reportDeprecated`, and modules
  that pass strict mode carry `# pyright: strict`; the A10 modules are exempt by configuration. No runtime change.
- Typing: `cache` has concrete return annotations (`scan` gives `tuple[int, list[bytes]]`, `store` / `store_bytes`
  give `bool | None`, `get_set_members` gives `set[Any] | None`) and `data_type` overloads on `get` and
  `get_dict_item`, on the module functions and `ValkeyCache`. The failure log of a swallowed error names the function
  by its qualified name.
- Typing: `ObjectStorageStream` accepts any `SupportsNext` stream; `soap_client` is annotated
  `AsyncGenerator[AsyncClient]`; `ZeepAuditPlugin` hooks are typed with `lxml.etree._Element` and `@override`;
  `api_client.request` parameters use the `Headers`, `Query`, `JsonBody` and `FormBody` aliases. `types-lxml` joins
  the dev group.
- `python3_commons.db.Base` is a typed `DeclarativeBase` subclass (with `AsyncAttrs`) instead of `declarative_base()`,
  so type checkers see mapped models and their constructors; it still uses the shared `metadata`, so `Base.metadata`
  and Alembic `target_metadata` are unchanged. Session makers are typed `async_sessionmaker[AsyncSession]`.
- Public signatures name runtime-importable types, so `typing.get_type_hints` and `inspect.signature` resolve the
  public annotations at runtime (permissions, db and db.models, object_storage, soap_client, cache, audit,
  zeep_audit, api_client, auth, serializers.msgspec); no module uses `from __future__ import annotations` any more.
- On Python 3.15, importing `python3_commons.cache`, `python3_commons.api_client` or `python3_commons.auth` no longer
  imports valkey or aiohttp until first use (PEP 810 `__lazy_modules__`); 3.14 imports eagerly. An installed but
  broken valkey or aiohttp raises its own `ImportError` on first use instead of at import.
- Optional-extra import guards go through one helper (`find_spec` on the extra's top-level modules). A missing extra
  still raises `RuntimeError('Install python3-commons[<extra>] to use this feature')`; an installed dependency that
  fails to import now raises its own `ImportError` instead of that misleading `RuntimeError`. `python3_commons.auth`
  now names the `authn` extra in its hint instead of `api-client`.
- Dependency ranges no longer block consumers: `~=` pins and the `aiohttp<3.15` cap became `>=` floors with
  major-version caps (`aiohttp<4`, `SQLAlchemy<3`, `pydantic<3`, `pydantic-settings<3`, `valkey<7`, `msgpack<2`,
  `lxml<7`, `zeep<5`, `requests<3`, `msgspec<1`, `asyncpg<1`, `object-storage-client<0.5`); `certifi` is uncapped.
  The floors pass the test suite in a lowest-direct resolution, so the project template's current pins resolve. The
  extras themselves are unchanged. `pydantic` is declared as a direct dependency, and the testing group has floors.
- Importing `python3_commons.api_client` (and so `python3_commons.auth`) no longer imports `python3_commons.audit` or
  `object_storage`: audit is imported on the first audit write, and `object_storage` only when an audit root is set.
  A missing object-storage extra no longer breaks importing `api_client`; with audit configured and the extra
  missing, the record is dropped with the rate-limited WARNING and the request continues. Code that patched
  `python3_commons.audit.object_storage` patches `python3_commons.object_storage` instead.
- `ApiClientSettings.audit_path` and `ObjectStorageSettings.work_path` (`OBJECT_STORAGE_WORK_PATH`) are normalised
  to a URL when the settings are built: repeated slashes collapse, the scheme is lowercased and a value without a
  scheme becomes a `file://` URL. A blank value becomes `None` (audit off, no work path), and a root that is only
  slashes (`/`, `s3://`, `file:///`) fails validation. Code that compares the raw value sees the normalised form.
- `audit.store_audit(key, data, *, root=None)` is the single audit write path; it raises on storage errors (the API
  client's audit step and `write_audit_data` wrap it). `write_audit_data` gains a keyword-only `root=`.
- API client audit is best-effort and bounded: a failing or hanging audit write no longer fails or stalls the request.
  Each write is limited by `API_CLIENT_AUDIT_TIMEOUT` (default 5 s; `request(audit_timeout=...)` per call), and a
  dropped record logs one WARNING without a traceback, at most once per 60 s per process, with the number suppressed.
- Plain `GET` requests with `audit_name` now also write a request record (before, a `GET` without headers or query
  wrote only the response).
- Response bodies are stored as the raw bytes received instead of decoded text, capped at
  `API_CLIENT_AUDIT_MAX_BODY_BYTES` (default 1048576) with a truncation marker.
- Audit keys use the URI path only (no query string or fragment); `audit_name` and each path segment are
  percent-quoted, `.` and `..` become `_`, and segments are cut at 200 characters. `write_audit_data` drops a key
  with a `.` or `..` segment and logs a WARNING.
- `ZeepAuditPlugin` no longer keeps unbounded, undrained background tasks in a module-global set: writes are tracked
  per plugin, capped by the new keyword-only `max_pending` (default 100; extra records are dropped with the same
  rate-limited WARNING as other dropped audit records), bounded by `API_CLIENT_AUDIT_TIMEOUT`, and `soap_client()`
  drains the plugins on exit, so every audit write finishes (or is cancelled and logged) before the context closes.
  New `ZeepAuditPlugin.drain(timeout=5.0)`.
- `api_client.request` sends every method through one `client.request()` call: a `uri` without a leading slash
  joins correctly, `json={}` and `json=[]` are sent, a caller's `Content-Type` (any case) is kept instead of being
  overridden, and `json` passed to a `GET` is now sent instead of being silently dropped.
- **Action required:** object-storage keys are validated before use. A key containing `://` (including the
  leading-slash form `/s3://other/x`), a NUL, or a `.` or `..` segment (also percent-encoded, or written with
  backslashes) raises `ValueError`: `get_object`, `put_object`, `remove_object` and `get_object_stream` could
  read, write or delete outside `work_path` on `file://` and in other buckets. An empty key raises `ValueError` in
  those four functions.
- **Action required:** `remove_objects` refuses a prefix that is empty after normalisation (`''`, `'/'`, `'//'`, or a
  `f'{tenant}/'` with an empty tenant) or that resolves to the storage root, and raises `ValueError` instead of
  deleting everything under `work_path`; pass `remove_all=True` to wipe on purpose. Names in `object_names` that are
  empty or invalid are returned as error entries and never deleted.
- `normalize_root` (`audit.normalize_audit_root`, and the new `object_storage.normalize_root` alias) lowercases the
  scheme, so `S3://bucket` no longer yields `s3://S3://bucket/...`, leaves the authority untouched, turns a root
  without a scheme into a `file://` URL, and raises `ValueError` for an empty or blank root
  (settings map blank to `None` before calling it). `list_objects` normalises its root and addresses each item
  relative to the listed URL, so a nested `data/data/x.txt` no longer returns the metadata of the top-level
  `data/x.txt`.
- `object_storage.list_objects` streams the listing (`list_objects_stream`, bounded memory) and fetches metadata
  concurrently (at most 16 at a time); items are typed (`ObjectInfo`, or `ObjectKey` with the new
  `with_metadata=False`, which skips the metadata calls). `get_objects` no longer nests `get_object_streams` and holds
  no stream open while your loop body runs. `get_object_streams` and `get_objects` close the listing when they are
  closed; wrap `get_object_streams(...)` in `contextlib.aclosing` when you may stop early.
- `object_storage` no longer logs an ERROR with a traceback before re-raising a failure: the exception (type
  unchanged) carries a note `object storage url: ...`, and the layer that handles it logs it once (for audit, the
  rate-limited WARNING). If you relied on those ERROR logs for alerting, log the exception where you catch it.
  `remove_objects(object_names=...)` deletes concurrently (16 at a time), and its error dicts gain an `'Exception'`
  key next to `'Key'` and `'Error'`.
- `get_object_stream` closes the native stream on exit (new `ObjectStorageStream.aclose()`), and exceptions raised in
  the caller's `async with` body are no longer logged as storage failures.
- `OIDCClient`: leaving `async with client` no longer closes the session under another task's in-flight
  `get_config`, `get_jwks` or `fetch_token`. The session is reference-counted and closes only when the last user has
  left and no call is in flight, so fastapi-commons' per-request `async with` is safe with no change there; a
  sequential `async with` still closes its session. A client reused on a new event loop while idle (a module-level
  instance across `asyncio.run()` calls or test loops) rebuilds its session and locks instead of raising "bound to a
  different event loop"; using it from a second loop while it is busy raises
  `RuntimeError('OIDCClient is in use on another event loop')`. The README recommends one client per loop, created in
  the lifespan and closed with `aclose()`.
- `OIDCClient` JWKS refresh: concurrent refreshes share one library-owned task (five callers with the identity provider
  down cause one fetch, not five), a cancelled caller no longer cancels the refresh for the others, and the refresh
  keeps the client's session open until it finishes. A failed refresh is remembered: until a backoff ends (1 s,
  doubling per consecutive failure, capped at `min_refresh_interval`, reset on success) `get_jwks` raises
  `ConnectionRefusedError('OIDC provider unavailable')` chained from the last failure without contacting the provider
  (503 in fastapi-commons). Expired keys are never served: once `jwks_cache_ttl` has passed and a refresh fails,
  `get_jwks` raises.
- `OIDCClient` with `authority_internal_host` rewrites every discovered endpoint (`token_endpoint`, `jwks_uri`,
  `userinfo_endpoint`, `introspection_endpoint`, `revocation_endpoint`, `end_session_endpoint`) to the internal host;
  before, only `jwks_uri` was rewritten, so `fetch_token` posted to the external host. `get_config()` now returns the
  rewritten endpoints (`issuer` is unchanged).
- `OIDCClient.get_jwks(force_refresh=True)` is rate-limited by `min_refresh_interval` (60 s by default): inside that
  window after the previous attempt, or while a refresh runs, it returns the cached keys. Pass
  `min_refresh_interval=0` to disable the rate limit and the backoff.
- README: documented S3 credentials, the audit settings, `ZeepAuditPlugin`, typed `msgspec` decoding, the GET-only
  status mapping and the supported Python range.

### Added

- `python3_commons` ships a `py.typed` marker (PEP 561), so type checkers use its inline annotations, and every public
  module defines `__all__`: `from module import *` binds only the public names, and pyright's
  `reportPrivateImportUsage` now applies. Names served lazily with a deprecation warning (the legacy `conf` settings,
  `S3Settings` / `get_s3_settings`, `api_client.audit` / `api_client.s3_settings`, the zeep names in `audit`) are
  deliberately left out of `__all__`; import them by name.
- `audit.normalize_audit_root()` canonicalises the audit root: scheme prefix, no trailing or repeated slashes, and a
  root without a scheme as a local `file://` URL.
- `audit.write_audit_data_soon()` stores audit data from synchronous code, in the background when an event loop runs.
- `zeep_audit.__all__`.
- `api_client.__all__`; `zeep_audit.get_history_plugin()` also accepts a sync `zeep.Client`.
- `decorators.retry(*, attempts=3, retry_on=(ConnectionError, TimeoutError), delay=0.1, backoff=2.0, max_delay=5.0,
  jitter=True)` for sync and async functions: exponential backoff with full jitter, a WARNING per failed attempt,
  never retries `CancelledError`. A 429 from `api_client` is an `InterruptedError`; add it to `retry_on` to retry it.
  `decorators.log_execution_time` uses `perf_counter`, logs the qualified name, takes an optional `level=` and works
  on sync functions too (`@log_execution_time` and `@log_execution_time(level=...)`).
- `text.slugify`; a keyword-only `tz=` on `dates.date_from_string` / `datetime_from_string` (applied to naive
  datetimes); `include_hidden=` and `follow_symlinks=` on `fs.iter_files`. A date that matches neither accepted format
  raises one `ValueError` naming both.
- `soap_client.abuild_soap_client()` builds a zeep `AsyncClient` in a worker thread; `build_soap_client()` is
  documented as blocking.
- `api_client.request(..., raise_for_status=None)`: `True` maps non-ok statuses to the builtin exceptions for every
  method, `False` always yields the response, `None` keeps today's behaviour (`GET` only). `OIDCClient.fetch_token`
  passes `False`.
- `OIDCClient(session=...)` takes a borrowed `aiohttp.ClientSession`, which the client never closes or replaces, and
  `OIDCClient.aclose()` closes the owned session (idempotent; the lifespan hook).
- `serializers.json.CustomJSONEncoder` encodes `UUID`, `set`/`frozenset`, `Enum` (as `.value`) and pydantic models
  (`model_dump(mode='json')`). `serializers/msgpack.py` and `serializers/msgspec.py` encode dataclasses through it, so
  dataclass fields of these types now encode instead of raising `TypeError`.
- Optional encryption at rest for everything python3-commons writes to object storage, audit records included: set
  `OSC_KEYRING_FILE` (the `osc` CLI's own variable) to a keyring file and `object_storage.get_client()` returns an
  encrypting client. There is no python3-commons setting, and `OSC_KEYRING_COMMAND` is not supported. A keyring that
  cannot be loaded makes `put_object` raise `EncryptionError`, and audit drops the record with its rate-limited
  WARNING, never writing plaintext; key rotation needs no restart. With the keyring on, reads still accept plaintext
  objects: `get_object`, `get_object_stream` and what is built on them log one WARNING naming the object and read it
  through the plain view, and `list_objects` reports the stored size. See the README section "Encrypting object
  storage", including the security consequence of that fallback.
  **Action required** for deployments that already set `OSC_KEYRING_FILE` for the `osc` CLI in the app's
  environment: python3-commons starts encrypting on upgrade, so unset it for the app or make sure every reader of
  those objects can decrypt them.
- Cached settings getters in `python3_commons.conf`: `get_common_settings()`, `get_db_settings()`,
  `get_valkey_settings()`, `get_s3_settings()`, `get_api_client_settings()`, `get_object_storage_settings()` and
  `get_oidc_settings()`. Importing python3-commons no longer builds or validates settings, so a bad environment value
  (for example `DB_PORT=abc`) no longer breaks `import python3_commons.auth` and friends; call the getters in the
  application lifespan to keep fail-fast startup.
- `AsyncSessionManager.aclose()`, `async with AsyncSessionManager(...)`, `AsyncSessionManager.session(name)` and the
  keyword-only `probe_connection`.
- `serializers.json.dumps()` and `loads()`, msgspec-based. They are not wire-compatible with `CustomJSONEncoder` (UTC
  datetimes end in `Z`, NaN becomes `null`), so `api_client` keeps using the encoder.
- `OIDCClient.from_settings(settings=None, **overrides)` builds a client from `OIDCSettings` (default: the shared
  `get_oidc_settings()`), mapping `jwks_refresh_interval` to `min_refresh_interval` and the new
  `OIDCSettings.jwks_cache_ttl` (`OIDC_JWKS_CACHE_TTL`, default 300 s); it raises `ValueError` when `authority_url`
  or `client_id` is missing. `OIDCClient` now holds its client secret as a `SecretStr`.
- Optional `TokenData` claims `nbf`, `azp`, `scope`, `jti`, `sid` and `groups`, and `TokenData.has_role()`.
- Typed OIDC documents: `OIDCProviderMetadata`, `JWKS` and `JWK` msgspec structs with
  `OIDCClient.get_provider_metadata()` and `get_key_set()` (`get_config()` and `get_jwks()` still return dicts).
  New grants: `OIDCClient.fetch_client_credentials_token(*, scope=None, audience=None)` and
  `refresh_token(refresh_token)`, with the same error mapping as `fetch_token`. `OIDCTokenResponse.id_token` is
  optional, and `token_type` defaults to `'Bearer'` when the provider omits it.
- `OIDCClient(min_refresh_interval=60.0)` and `OIDCClient.get_signing_key(kid)`, which returns the JWK or `None`; an
  unknown `kid` refreshes the key set at most once per `min_refresh_interval` (key rotation), and if the keys have
  expired and the provider is down it raises `ConnectionRefusedError`.
- `object_storage.ObjectStorage(root, *, client=None, profile=None)`: one normalised root and an injectable client,
  under the 1.0 key rules (relative keys only, no prefix deduplication, no deprecated parameters); `profile` is passed
  to `ObjectStorageClient(profile=)` and `OSC_KEYRING_FILE` still applies. `object_storage.default_client()` (cached,
  `cache_clear()`) and `set_default_client(client | None)`, which the module functions, `ObjectStorage` and audit
  writes all follow. `get_client()` is an alias of `default_client()`; tests that reset `object_storage._CLIENT`
  call `default_client.cache_clear()` or `set_default_client(None)` instead.
- `object_storage.remove_objects(..., raise_errors=True)` raises the per-name failures as an `ExceptionGroup`.
- `object_storage.put_object_stream(work_path, path, chunks, *, size_hint=None)` writes an async iterable of chunks
  through the same client as `put_object` (so the keyring applies).
- `object_storage.remove_objects(..., remove_all=False)`, and a keyword-only `allow_absolute` on `build_url` and the
  object-storage functions that take a path or prefix.
- `cache.ValkeyCache`: a Valkey cache object with `from_settings()`, `aclose()` and `async with`, and every cache
  command as a method under the 1.0 contract: `store_*` return `bool`, `delete*` return `int`, collection writes
  replace (DEL, write and EXPIRE in one transaction; an empty collection deletes the key), `append_sequence` /
  `update_dict` merge, a non-positive ttl raises `ValueError`, `fail_open` swallows only transient errors
  (connection, timeout, busy loading) and a missing DSN raises `RuntimeError`. It applies socket timeouts,
  keepalive, a health check and retries to plain-DSN clients too. The module functions are unchanged until 1.0.
- New `ValkeySettings` fields: `sentinels`, `sentinel_service_name`, `sentinel_password`, `socket_timeout`,
  `socket_connect_timeout`, `health_check_interval`, `retries`, `max_connections` (a bounded, blocking pool) and
  `pool_timeout`; the defaults are today's values.
- `cache.get_default_cache()`, `set_default_cache()` and `close_default_cache()`: one `ValkeyCache` per event loop,
  built from the settings on first use; and `cache.__all__`.
- `cache.append_sequence(name, *items, ttl=None)` and `cache.update_dict(name, mapping, ttl=None)`, the explicit merge
  API; `get_sequence(..., data_type=)` and `get_set_members(name, data_type=)`; cache `ttl` also accepts `timedelta`.
- `cache.close_client()` closes the running loop's Valkey client and its sentinel connections and forgets them: the
  supported teardown for lifespans and tests.
- `db.models.load_models()` returns the shared metadata with every library model registered and every foreign key
  resolved; Alembic `env.py` should call it and use the result as `target_metadata`. The eight model classes are
  re-exported from `python3_commons.db.models`. Importing any model module now registers the tables its foreign keys
  point to (they are built from the imported classes; constraint names are unchanged), and the permission helpers call
  `load_models()` on first use.

### Deprecated

- `api_client.request`: a non-ok response to a non-`GET` request with the default `raise_for_status=None` emits a
  `DeprecationWarning`; in 1.0 every method raises (pass `raise_for_status=False` to keep getting the response).
  Passing both `json` and `data` emits a `DeprecationWarning` and raises `ValueError` in 1.0 (until then a non-empty
  `json` wins, otherwise `data` is sent).
- `object_storage.list_objects` placeholder values (`LastModified=datetime.now()`, naive, and `Size=0` for a missing
  object, failed metadata or a missing timestamp): such a call logs a WARNING and emits a `DeprecationWarning`. In 1.0
  missing objects are skipped, unavailable metadata is `None`, and only aware datetimes are returned.
- `object_storage.put_object(data=None)` (`data` is required in 1.0) and its ignored `length` / `part_size` parameters
  (removed in 1.0): pass `b''` for an empty object and drop `length` / `part_size`.
- Absolute object-storage URLs (`s3://`, `file://`, `gs://`, `az://`) as the `path`, `prefix` or object name emit a
  `DeprecationWarning` and raise `ValueError` in 1.0; pass `allow_absolute=True` to opt in until then, or use a
  relative key.
- The object-storage prefix deduplication (`build_url('s3://b/data', 'data/x.txt')` returning `s3://b/data/x.txt`)
  emits a `DeprecationWarning` naming the current and the 1.0 URL whenever they differ; the current URL is still
  returned. The heuristic is removed in 1.0.
- **Action required:** constructing `AsyncSessionManager` without `probe_connection` warns: the pre-acquire connection
  probe becomes opt-in in 1.0. Pass `probe_connection=True` to keep it (or `False`), and close the manager at shutdown
  with `async with` or `await manager.aclose()`.
- The `python3_commons.conf` module attributes `settings`, `db_settings`, `valkey_settings`, `s3_settings`,
  `api_client_settings` and `object_storage_settings`, and `python3_commons.api_client.s3_settings`: they warn on
  access and are removed in 1.0. Use the getters (for example `get_db_settings()`), or build your own settings
  instance. `ObjectStorageSettings` itself is not deprecated.
- Names moved out of `python3_commons.helpers` (the aliases warn on access and are removed in 1.0):
  `python3_commons.dates` (`date_from_string`, `datetime_from_string`, `date_range`), `python3_commons.decimals`
  (`round_decimal`), `python3_commons.decorators` (`log_execution_time`), `python3_commons.text` (`to_snake_case`,
  `parse_string_list`) and `python3_commons.urls` (`request_to_curl`, `replace_origin`; the only one that imports
  pydantic). `from python3_commons.helpers import *` still binds them and warns once per name. `SingletonMeta` and
  `tries` stay in `helpers` and do not warn.
- `text.to_snake_case` warns when its 1.0 result would differ (use the new `text.slugify` to keep today's output);
  `decimals.round_decimal` warns on `float` input; `fs.iter_files` warns when it yields a hidden file or follows a
  directory symlink without an explicit `include_hidden` / `follow_symlinks` (pass them to keep today's behaviour
  silently).
- `async_functools.LRUAsyncCallable`, `LRUAsyncBoundCallable`, `BaseLRUAsyncCallable`, `CachedLRUAsyncCallable`,
  `UncachedLRUAsyncCallable` and the `AC` / `AC_invariant` TypeVars (removed in 1.0): use `AsyncLRUCache` and
  `BoundAsyncLRUCache`.
- Leaked internals are private now and their old names warn (removed in 1.0): `helpers.logger`, and in
  `serializers.msgspec` `enc_hook`, `ext_hook`, `MSGPACK_ENCODER`, `MSGPACK_ENCODER_NATIVE`, `MSGPACK_DECODER` and
  `MSGPACK_DECODER_NATIVE`. Use `serialize_msgpack` / `deserialize_msgpack` (or the `_native` variants) instead.
- `python3_commons.dist_name` and the leaked `python3_commons.importlib` attribute (removed in 1.0): use
  `'python3-commons'` and import `importlib` yourself.
- `python3_commons.api_client.audit` (removed in 1.0): import `python3_commons.audit` instead.
- **Action required:** passing `api_client.request()` arguments after `uri` positionally warns; they are keyword-only
  in 1.0. Pass `query`, `method`, `headers`, `json`, `data`, `timeout`, `audit_name`, `audit_timeout` and
  `raise_for_status` by keyword.
- `ZeepAuditPlugin.store_audit_in_s3` (removed in 1.0).
- `conf.S3Settings` and `conf.get_s3_settings()` (removed in 1.0): nothing in python3-commons needs them,
  object-storage-client reads `S3_*`/`AWS_*` itself. The `settings` argument of `audit.write_audit_data()`, which was
  unused, warns when passed and is removed in 1.0.
- In 1.0 `TokenData` becomes frozen and keyword-only, and `roles` comes back sorted with `groups` merged in (this
  cannot warn at runtime). Construct `TokenData` by keyword, do not mutate instances, and do not depend on the order
  of `roles`.
- `AsyncSessionManager.get_session_context` (use `session()`) and reading `engines` / `session_makers`; removed in 1.0.
- In 1.0 `serializers.msgspec` raises `TypeError` instead of `NotImplementedError` for an unsupported type on encode and
  an unknown extension code on decode (under fastapi-commons' `handle_exceptions` that is an unhandled 500 instead of
  501). Callers that catch `NotImplementedError` from these functions should also catch `TypeError`.
- `serializers.json.CustomJSONEncoder` stringifying a `type` or a `socket` emits a `DeprecationWarning` (attributed to
  the first caller outside the library); it raises `TypeError` in 1.0.
- `cache` collection writers (`store_sequence`, `store_dict`, `store_set`) writing to an existing key warn once per
  process: in 1.0 they replace instead of merging and return `bool`; use `append_sequence` / `update_dict` to keep
  merging. Collection `ttl=0`, and `store` / `store_bytes` with `ttl <= 0`, warn and raise `ValueError` in 1.0. A
  swallowed Valkey `AuthenticationError` warns; 1.0 raises it.
- The `cache.get_sequence(..., _type=...)` keyword (removed in 1.0): use `container=`.
- `cache.AsyncValkeyClient` (removed in 1.0): use `get_valkey_client()`. It is no longer a singleton, so
  `AsyncValkeyClient._instances` is gone.
- `db.models.common.UTCNow` (removed in 1.0): use `sqlalchemy.func.now()`. It now compiles to `CURRENT_TIMESTAMP` on
  every dialect and has a SQL compilation cache key; instantiating it warns.
- In 1.0 a missing Valkey DSN raises `RuntimeError` instead of `ValueError` (a configuration error, unhandled under
  fastapi-commons' `handle_exceptions` instead of a 400).

### Fixed

- `http(s)` roots with a port or an IPv6 host, and `az`/`abfss` authorities, are no longer rewritten
  (`https://host:8443/p` became `https://host/8443/p`). `file:///` is no longer turned into `s3://file`. Roots and
  absolute keys on every scheme the client supports (`gcs`, `wasb(s)`, `abfs(s)`, `http(s)`) are recognised instead of
  getting `s3://` prepended.
- A failed audit write is logged and no longer propagates.
- `audit.__all__` no longer lists the lazily loaded zeep names, so `from python3_commons.audit import *` does not
  import `zeep`.
- Unit tests no longer leak `aiohttp` client sessions.
- `generators.tuple_csv_stream` honours its `dialect`: the keyword defaults (`delimiter=','`,
  `lineterminator='\r\n'`, ...) were always passed to `csv.writer` and overrode it, so `excel-tab` and `unix` gave
  comma-separated CRLF output. Only the format parameters the caller passes now override the dialect (typed as
  `CsvFormatParams`), `dialect` is annotated `str | type[csv.Dialect]`, and the input is any
  `AsyncIterable[Sequence[object]]`. A source async generator is closed with `aclosing` when the stream is closed
  early, so its `finally` runs in the caller's task.
- `api_client.request` no longer forwards `timeout=None` to aiohttp, which overrode the session timeout and left
  requests unbounded. `timeout=None` now means the session default and `ClientTimeout()` means no limit; the
  annotation is `ClientTimeout | float | None`.
- `OIDCClient` bounds its configuration, JWKS and token requests with its `timeout` argument, so fastapi-commons'
  `OIDC_TIMEOUT` takes effect. A slow identity provider now raises `TimeoutError`, which fastapi-commons' token
  verifier answers with 503.
- `OIDC_SCOPE` and `OIDC_AUDIENCE` accept comma-separated values (and a JSON list when the value starts with `[`):
  the template's `OIDC_SCOPE="openid,profile,email"` no longer fails at import. `parse_string_list` strips items, drops
  empty ones and always returns a tuple.
- `DBSettings.password` reads `DB_PASS` or `DB_PASSWORD`, and accepts `pass`, `password` and `DB_PASS` as keyword names.
- `CommonSettings.logging_level` is upper-cased and validated against the known level names.
- **Action required:** a `DBSettings` subclass with its own prefix (for example `RO_DB_`) reads only its own prefixed
  variables (`env_prefix_target='all'`) and no longer inherits `DB_PASS`. Read-only deployments set `RO_DB_PASS`.
- Settings outside these bounds now fail validation when they are built: `OIDC_TIMEOUT` > 0,
  `OIDC_CONNECTION_LIMIT` >= 0, `DB_POOL_SIZE` >= 0, `DB_MAX_OVERFLOW` >= -1, `DB_POOL_RECYCLE` >= -1,
  `DB_POOL_TIMEOUT` >= 0, `DB_STATEMENT_TIMEOUT` >= 0 (for example `OIDC_TIMEOUT=0` is rejected).
- `soap_client`: WSDL and XSD loading no longer starts a thread and an event loop per fetch; it goes through zeep's
  requests-based `Transport.load`, so local paths, `file://` URLs and the zeep cache work. `soap_client()` builds the
  client off the event loop (`abuild_soap_client()`), so a slow WSDL no longer stalls other tasks.
  `TransportConfig.timeout` and `operation_timeout` reach the base transport, `verify_ssl` and `proxy` apply to the
  WSDL fetches, and `transport.settings(timeout=...)` is honoured by `post()` and `get()`. Response headers are
  case-insensitive (a lowercase `content-type: multipart/related` is recognised), with duplicates folded by `', '`.
- `log.filters.filter_maker` is `MaxLevelFilter`, a `logging.Filter`. It accepts level names in any case (`'info'`)
  or ints, and an unknown level raises `ValueError` when `dictConfig` builds it, instead of a `TypeError` on every log
  call. The dotted path and the `level=` keyword are unchanged.
- `log.formatters.JSONFormatter`: emits `stack` for `stack_info=True`; no longer leaks `asctime` or `message` from a
  record another handler formatted; reserved keys (`message`, `level`, `logger`, `timestamp`, `exc_*`, `stack`) can no
  longer be overwritten by `extra`, and colliding extras move under an `extra` key; values are encoded the same at top
  level and nested (bytes as base64, exceptions as `Type: msg`, anything else as `repr`); when `msg % args` fails it
  emits the raw `message`, `args` and `message_error`; `fmt` and `datefmt` from `dictConfig` are accepted and
  ignored. JSON log consumers may see the new `stack`, `extra`, `message_error` and `args` keys, and top-level bytes
  now come out base64-encoded.
- `cache`: the Valkey client is kept per event loop, so a client is never reused across loops (consecutive
  `asyncio.run()` calls, test loops), and entries of closed loops are evicted on every `get_valkey_client()` call.
  `get_valkey_client()` must now be called with a running event loop. With no `VALKEY_DSN` or
  `VALKEY_SENTINEL_DSN` it raises `ValueError('Set VALKEY_DSN or VALKEY_SENTINEL_DSN')` (the type is unchanged).
- `cache`: collection writes (`store_sequence`, `store_dict`, `store_set`) send the write and `EXPIRE` in one
  `MULTI/EXEC` transaction, so a connection or timeout failure between them no longer leaves a key without its TTL
  (merge semantics unchanged). Decode errors (msgspec or pydantic validation) count as a cache miss and log a WARNING.
  `get_string` returns `''` for an empty stored value instead of `None`.
- `async_functools.async_lru_cache`: cancelling or timing out one caller no longer cancels the other callers waiting
  for the same key. This also fixes `InvalidStateError` in the first caller, "Future exception was never retrieved"
  log noise, waiters hanging on a `BaseException`, and "attached to a different loop" errors when two event loops call
  the same cached function. Behaviour changes: concurrent calls share one computation that runs in a Task the cache
  owns, which outlives a cancelled first caller and is cancelled only when its last waiter goes away; it runs in a copy
  of the first caller's context, so context variables the cached function sets no longer reach the caller and later
  joiners get a result computed in the first caller's context (do not cache functions that depend on request-scoped
  context variables); running computations are per event loop, while cached values are shared by every loop. A cache
  instance is documented as not thread-safe.
- `helpers.request_to_curl` (used for audit records) no longer raises on a non-UTF-8 body, which aborted the real
  request when audit was on; such a body renders as `--data-binary @body.bin` with a size and sha256 comment. It also
  merges `query` into a URL that already has a query string (no second `?`), emits text bodies with `--data-raw` (a
  leading `@` is not read as a file), emits an empty JSON body (`{}`), and adds `Content-Type: application/json` only
  when the caller did not set one. Header, cookie and body values are still rendered unchanged.
- **Action required:** `created_at` and `updated_at` on `BaseDBModel` and `BaseDBUUIDModel` use `func.now()`
  (`CURRENT_TIMESTAMP`, a timestamptz) instead of `TIMEZONE('utc', CURRENT_TIMESTAMP)`, which returned a naive
  timestamp that PostgreSQL cast back with the session TimeZone: on any server, database or role whose TimeZone was
  not UTC, stored timestamps were off by the UTC offset.
  - The `created_at` DDL default of existing tables does not change on upgrade, and Alembic autogenerate does not
    detect it: add one hand-written revision with `op.alter_column(t, 'created_at', server_default=sa.text('now()'))`
    for each table (in the project template: user_groups, user_profiles, users and api_keys). `updated_at` is
    corrected on upgrade, because `onupdate` is client-side.
  - Deployments whose TimeZone was not UTC (check `SHOW timezone` and `pg_db_role_setting`) can repair stored rows
    with `UPDATE t SET created_at = (created_at AT TIME ZONE '<tz>') AT TIME ZONE 'UTC'`, and the same for
    `updated_at`. Rows written in a DST fall-back hour stay ambiguous.
- `BaseDBModel` and `BaseDBUUIDModel` set `__mapper_args__ = {'eager_defaults': True}`, so `updated_at` is fetched with
  RETURNING after a unit-of-work update and reading it in async code no longer raises `MissingGreenlet`. Subclasses
  that define their own `__mapper_args__` merge it: `{**BaseDBUUIDModel.__mapper_args__, ...}`.

## [0.1.0]

### Changed

- Breaking change: made absolute path generation for object storage to be optional.

## [0.0.1]

### Added

- Initial commit
