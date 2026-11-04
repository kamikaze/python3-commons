# python3-commons

Re-usable code for Python 3 projects.

Supported Python versions: `>=3.14.10,<3.16`.

## Installation

```bash
uv add python3-commons
```

### Optional Dependencies

Some features require extra dependencies. You can install them individually or all at once:

- `api-client`: For `python3_commons.api_client`
- `audit`: For `python3_commons.zeep_audit` (`ZeepAuditPlugin`; installs `zeep`, `lxml` and the `object-storage` extra)
- `authn`: For `python3_commons.auth`
- `authz`: For `python3_commons.permissions`
- `cache`: For `python3_commons.cache`
- `database`: For `python3_commons.db`
- `object-storage`: For `python3_commons.object_storage`
- `soap-client`: For `python3_commons.soap_client`
- `all`: Install all optional dependencies

```bash
uv add "python3-commons[all]"
```

## Features

### Async LRU Cache

An LRU cache decorator for asynchronous functions that handles `await` and prevents the dogpile effect (mitigating multiple concurrent calls for the same key by waiting for the first one to finish).

```python
from python3_commons.async_functools import async_lru_cache

@async_lru_cache(maxsize=128)
async def fetch_expensive_data(item_id: int):
    # This function will only be called once for a given item_id 
    # even if multiple tasks await it simultaneously.
    return await db.get(item_id)

# Usage
result = await fetch_expensive_data(42)
```

### API Client

An async `request()` context manager for `aiohttp` with standardized error mapping to Python exceptions and optional audit logging to Object Storage.

#### Standardized Exception Mapping

For `GET` requests, HTTP error status codes and network failures are mapped to built-in Python exceptions (until 1.0, the status mapping applies to `GET` only: responses to other methods are yielded as they are, whatever the status, so check `response.status` yourself; network failures are mapped for every method):
- `400 Bad Request` -> `ValueError`
- `401 Unauthorized`, `403 Forbidden` -> `PermissionError`
- `404 Not Found` -> `LookupError`
- `429 Too Many Requests` -> `InterruptedError`
- Other 4xx/5xx responses -> `aiohttp.ClientResponseError`
- Connection errors -> `ConnectionRefusedError` / `ConnectionResetError`

#### Enabling Audit Logging

Audit logging captures the outgoing request (formatted as an executable `curl` command) and the incoming response body, writing them asynchronously to Object Storage.

Audit data is written only under `ApiClientSettings.audit_path` (`API_CLIENT_AUDIT_PATH`), a full object storage path such as `s3://bucket/audit`. A value without a scheme is a local filesystem path (`~` is expanded, a relative path resolves against the working directory) and is normalised to a `file://` URL:
`<audit_path>/<YYYY/MM/DD>/<audit_name>/<uri>/<method>_<timestamp>_<request_id>_{request,response}.txt`

`OBJECT_STORAGE_WORK_PATH` is not used for audit. Without `API_CLIENT_AUDIT_PATH`, audit is disabled.

To enable audit logging:
1. **Configure Object Storage & S3 credentials**:
   ```bash
   # API client audit path (full object storage path)
   export API_CLIENT_AUDIT_PATH="s3://bucket/audit"

   # S3 credentials
   export S3_ACCESS_KEY_ID="your-access-key-id"
   export S3_SECRET_ACCESS_KEY="your-secret-access-key"
   export S3_REGION="us-east-1"
   # Optional configurations:
   # export S3_ENDPOINT="http://localhost:3900"          # S3-compatible store; an http:// endpoint enables plain HTTP
   # export S3_ADDRESSING_STYLE="virtual"                # virtual-hosted addressing (default: path); AWS_VIRTUAL_HOSTED_STYLE_REQUEST applies when it is unset
   # export AWS_ALLOW_INVALID_CERTIFICATES="true"        # skip TLS certificate verification
   # export S3_ALLOW_HTTP="true"                         # allow plain HTTP (only the exact value "true" is honoured)
   ```
   Credentials and storage options are read from the process environment by
   [object-storage-client](https://pypi.org/project/object-storage-client/), not from `s3_settings` (no code path
   in python3-commons reads `S3Settings`). `object_storage.get_client()` builds a profile-less `ObjectStorageClient()`,
   which signs with these variables (`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` also work, and
   `AWS_REGION` and `AWS_ENDPOINT` can stand in for `S3_REGION` and `S3_ENDPOINT`).
   See its [configuration docs](https://github.com/bixority/object-storage-client/blob/main/docs/CONFIGURATION.md)
   for the full list (GCS and Azure included).

   `ObjectStorageClient(profile="NAME")` switches `s3://` to profile-only credentials: the named AWS profile is then
   the only source, `S3_ACCESS_KEY_ID`/`AWS_ACCESS_KEY_ID` are not used and `AWS_PROFILE` is not read. See
   [AWS profiles and SSO](https://github.com/bixority/object-storage-client/blob/main/docs/CONFIGURATION.md#aws-profiles-and-sso).
   `python3_commons.object_storage` always uses the profile-less client.

   The audit path can also be configured directly in Python:
   ```python
   from python3_commons.conf import get_api_client_settings

   get_api_client_settings().audit_path = "s3://bucket/audit"
   ```

   Audit data is stored raw: the records hold the request and response as sent, including `Authorization` headers, cookies, OIDC passwords, client secrets and tokens. To encrypt them at rest, set `OSC_KEYRING_FILE`, see [Encrypting object storage](#encrypting-object-storage).

   Audit keys are built without empty path segments: repeated or leading slashes are dropped, so `a//b` is stored as `a/b`.

   Audit is best-effort and bounded: a storage failure or a hang never fails or stalls the request. Each write is limited to `API_CLIENT_AUDIT_TIMEOUT` seconds (default `5`; `request(audit_timeout=...)` overrides it per call), and a dropped record logs one WARNING, at most once per 60 seconds per process, with the number of suppressed ones. Response bodies are stored as received (raw bytes), capped at `API_CLIENT_AUDIT_MAX_BODY_BYTES` (default `1048576`) with a truncation marker. Key segments built from the URI path and `audit_name` are percent-quoted (only `-_.~` stay as they are), `.` and `..` become `_`, and each segment is cut at 200 characters; the query string and fragment are not part of the key.

2. **Pass `audit_name` to `request()`**: Provide a non-empty string identifier (such as the target service or operation name) in the `audit_name` argument. If omitted (default `None`), audit logging is disabled for that call. If storage is not configured, audit logging is safely bypassed. Every request with `audit_name` writes a request record (a `curl` command), including plain `GET`s, and a response record unless the body is empty.

#### Usage

`request()` takes an `aiohttp.ClientSession` that you manage:

```python
from aiohttp import ClientSession
from python3_commons.api_client import request

async with ClientSession() as session:
    # 1. GET request with audit logging enabled
    async with request(
        session,
        base_url="https://api.example.com",
        uri="/users/123",
        method="get",
        headers={"Accept": "application/json"},
        audit_name="user_service",
    ) as response:
        user = await response.json()

    # 2. POST request with JSON body and audit logging
    async with request(
        session,
        base_url="https://api.example.com",
        uri="/users",
        method="post",
        json={"name": "Alice", "role": "admin"},
        audit_name="user_service",
    ) as response:
        result = await response.json()

    # 3. Request without audit logging (audit_name omitted)
    async with request(
        session,
        base_url="https://api.example.com",
        uri="/health",
        method="get",
    ) as response:
        status = await response.json()
```

### SOAP Client

Async SOAP client support for `zeep` (extra `soap-client`), with optional S3 auditing of the SOAP envelopes.

```python
from python3_commons.soap_client import soap_client

async with soap_client("https://example.com/service?wsdl") as client:
    result = await client.service.GetData(id=42)
```

#### SOAP audit

`ZeepAuditPlugin` (module `python3_commons.zeep_audit`, extra `audit`) is a `zeep` plugin that writes every
request and response envelope under `API_CLIENT_AUDIT_PATH`, the same setting as the API client audit
(see [Enabling Audit Logging](#enabling-audit-logging)); without it, nothing is stored. Pass it through `plugins`:

```python
from python3_commons.soap_client import soap_client
from python3_commons.zeep_audit import ZeepAuditPlugin

async with soap_client("https://example.com/service?wsdl", plugins=[ZeepAuditPlugin("billing")]) as client:
    result = await client.service.GetData(id=42)
```

Envelopes are stored as `<audit_path>/<YYYY/MM/DD>/<audit_name>/<operation>/<time>_<id>_{egress,ingress}.xml`.
`get_history_plugin(client)` returns the `zeep` `HistoryPlugin` attached to a client, or `None`.
Importing `python3_commons.audit` or `python3_commons.api_client` does not load `zeep` or `lxml`; only
`python3_commons.zeep_audit` does.

### Database Management

SQLAlchemy async engine and session management with pool tuning, health checks, dynamic query builders, and declarative base models.

```python
from python3_commons.db import AsyncSessionManager, is_healthy
from python3_commons.db.helpers import get_query
from python3_commons.conf import DBSettings

# Configuration
configs = {
    "default": DBSettings(
        dsn="postgresql+asyncpg://user:pass@localhost/db",
        pool_size=20,
        pool_timeout=30,
        statement_timeout=30,
    )
}
manager = AsyncSessionManager(configs)

# Usage
async with manager.get_session_context("default") as session:
    result = await session.execute(...)

# Engine health check (executes SELECT 1 with timeout)
healthy = await is_healthy(manager.get_engine("default"))

# Dynamic query filtering and sorting
where_clause, order_clauses = get_query(
    search={"name": "Alice"},
    order_by="-created_at,name",
    columns={
        # name: (column, case_insensitive, cast_func, exact_match)
        "name": (User.name, True, str, False),
        "created_at": (User.created_at, False, str, True),
    },
)
```

### Object Storage

Thin wrapper for `object-storage-client` supporting S3, GCS, Azure, and local file storage. Credentials and storage options come from the process environment, read by `object-storage-client` (see [Enabling Audit Logging](#enabling-audit-logging) for the S3 variables and its [configuration docs](https://github.com/bixority/object-storage-client/blob/main/docs/CONFIGURATION.md)). Storage functions accept a required `work_path` parameter to define the base storage location. `object_storage_settings` (`OBJECT_STORAGE_WORK_PATH`) is the setting for that location: your code reads it and passes it as `work_path`; the module functions do not read it themselves.

```python
from python3_commons import object_storage
import io

# Upload an object (bytes, any buffer, or io.BytesIO)
await object_storage.put_object(work_path="s3://my-bucket", path="uploads/file.txt", data=io.BytesIO(b"Hello World"))

# Download an object as bytes
content = await object_storage.get_object("s3://my-bucket", "uploads/file.txt")

# Stream an object
async with object_storage.get_object_stream("s3://my-bucket", "uploads/file.txt") as stream:
    data = await stream.read()

# List objects with a prefix
async for obj in object_storage.list_objects("s3://my-bucket", "uploads/"):
    print(obj['Key'])

# Delete single or multiple objects
await object_storage.remove_object("s3://my-bucket", "uploads/file.txt")
await object_storage.remove_objects("s3://my-bucket", object_names=["uploads/file1.txt", "uploads/file2.txt"])
```

`ObjectStorage` binds one normalised root and an optional client, and follows the 1.0 rules already: keys must be
relative (no absolute URLs, `.` or `..` segments), there is no prefix deduplication, and an empty `remove_objects`
prefix needs `remove_all=True`. Errors are raised with an `object storage url: ...` note and are not logged.

```python
from python3_commons.object_storage import ObjectStorage

storage = ObjectStorage("s3://my-bucket/uploads")  # or ObjectStorage(root, client=...), ObjectStorage(root, profile="dev")
await storage.put_object("file.txt", b"Hello World")
content = await storage.get_object("file.txt")
```

The module functions and `ObjectStorage()` share `object_storage.default_client()`; tests can swap it with
`object_storage.set_default_client(fake)` and restore it with `set_default_client(None)`.

#### Encrypting object storage

Set `OSC_KEYRING_FILE` to the path of a keyring file and everything python3-commons writes to object storage is
encrypted at rest, audit records included (one keyring for all of it). Nothing else needs configuring, and without the
variable nothing changes.

`OSC_KEYRING_FILE` is the variable of the `osc` CLI of [object-storage-client](https://pypi.org/project/object-storage-client/).
The Python binding does not read it, so `object_storage.get_client()` does: it reads the variable when the client is
first used (never at import) and returns an `EncryptedObjectStorageClient`. One variable therefore turns the keyring on
for both `osc` and the application, so setting it for `osc` in an application's environment also switches on
encryption in the application. `OSC_KEYRING_COMMAND` (like `OSC_REQUIRE_ENCRYPTION`) is read by the `osc` CLI only and
is not supported by python3-commons: a process that sets only `OSC_KEYRING_COMMAND` writes plaintext.

Create a keyring with the `osc` CLI (installed with cargo, it is not part of the wheel):

```bash
umask 077
osc keyring new > keyring
export OSC_KEYRING_FILE="$PWD/keyring"
```

On Unix the file must not be writable by its group, must not be accessible to others, and must be owned by the
running user (or root). Group read is allowed and the file may be a symlink. For Kubernetes Secret volumes, see the
Kubernetes section of object-storage-client's
[docs/ENCRYPTION.md](https://github.com/bixority/object-storage-client/blob/main/docs/ENCRYPTION.md)
(`defaultMode: 0440` with `fsGroup`, and a whole-volume mount, never `subPath`), and its "Core dumps" section for any
process that holds a keyring.

What to expect:

- Data is written unchanged, credentials included: audit records hold the raw request and response, `Authorization`
  headers, cookies, OIDC passwords, client secrets and tokens, by design. The keyring is what protects them.
- A keyring that cannot be loaded makes `put_object` raise `EncryptionError`. Audit drops the record with its
  rate-limited WARNING and the request goes on; the record is never written in plaintext. The failure is not cached:
  fix the file and the next call works, without a restart. An application that wants to refuse to start with a bad
  keyring calls `object_storage.get_client()` in its lifespan, in the worker and not before a fork.
- With the keyring set, `get_client()` returns an `EncryptedObjectStorageClient`. It has no pre-signed URL methods; use
  its `without_encryption()` for those.
- Object names are not encrypted (for audit records: the date, `audit_name`, URI, method and timestamp).
- `list_objects` reports the stored (ciphertext) size of an encrypted object as `Size`.
- Copy or move encrypted objects only through an encrypting client or `osc --keyring`. The header names the object's
  path, so a copy re-encrypts, the plain client refuses to copy an encrypted object to another name, and a `file://`
  object moved with `mv` becomes undecryptable.

##### Reading, and objects that are not encrypted

Reads always accept plaintext. When an object is not encrypted (written before the keyring was set, for example),
`get_object` and `get_object_stream` log one WARNING naming the object (`object storage: <url> is not encrypted; read
as plaintext`) and read it through `without_encryption()`, and `get_object_streams` and `get_objects` do the same.
That costs a second request. `UnknownKeyError` and `DecryptionError` are not retried and propagate. For a stream only
the first read falls back; a `NotEncryptedError` on a later read propagates.

Security consequence: with the keyring on, a planted plaintext object is accepted, with a WARNING. Anyone who can write
to the store can put a plaintext object under a name python3-commons reads, or replace an encrypted one, and it is read
as genuine. An encrypted object whose first three bytes are damaged is also read through the plain view, and its raw
stored bytes are returned. The keyring protects the confidentiality of what python3-commons writes, not the
authenticity of what it reads. A caller that needs authenticity reads through its own
`ObjectStorageClient().with_encryption(Keyring.from_file(path))`, which raises instead.

There is no read-back helper for audit records. Read them with `osc --keyring keyring get ...` or with your own
encrypting client. `UnknownKeyError` "may also indicate tampering": it is derived from unauthenticated header data.

##### Key rotation

No code and no restart: the keyring reloads its file within about 5 seconds. Run this under `umask 077`; `osc keyring
add` and `osc keyring activate` read the current keyring on stdin and write the new one to stdout.

```bash
osc keyring add < keyring > keyring.new && mv keyring.new keyring
# wait until every reader has the new key (each reader's Keyring.status() lists it)
osc keyring activate <new key ID> < keyring > keyring.new && mv keyring.new keyring
osc --keyring keyring enc reencrypt <root>/ --from <old key ID>     # for every root, see below
```

Remove an old key only after `osc enc scan` shows that no object uses it; otherwise reads raise `UnknownKeyError`.
Rotate long before about 2**32 wraps per key (`Keyring.wraps`). A different `OSC_KEYRING_FILE` value needs a restart.

`<root>/` is the normalised root plus a trailing slash, for every root python3-commons writes under: the audit root and
each `work_path` the application uses. `osc` takes store URLs, and python3-commons normalises every root to one: a
value without a scheme is a local path and becomes a `file://` URL (a relative path resolves against the working
directory). This prints the normalised form (`file:///var/audit/` here, `s3://bucket/audit/` for an `s3://` root):

```bash
python -c "from python3_commons.audit import normalize_audit_root as n; print(n('/var//audit/') + '/')"
```

### OIDC Authentication

Client for OpenID Connect authentication, supporting configuration fetching, JWKS, and token acquisition.

Create one client per event loop in your application lifespan, use it directly, and close it with `aclose()` on shutdown. The client owns one `aiohttp` session for its whole lifetime, so connections are reused.

```python
from python3_commons.auth import OIDCClient
from pydantic import HttpUrl

client = OIDCClient(
    authority_url=HttpUrl("https://auth.example.com/realms/myrealm"),
    client_id="my-app-client",
    client_secret="secret"
)

# in the application lifespan
token_response = await client.fetch_token(username="user", password="password")
print(token_response.access_token)

# on shutdown (idempotent)
await client.aclose()
```

`async with client:` around each call still works and is safe to share between concurrent tasks: the session is reference-counted, so it closes only when the last task has left and no call is in flight, and a task that leaves never cuts off another task's request. Each time concurrency drops to zero the session is closed and the next use builds a new one, so the per-call pattern pays for a new session on every sequential request; the lifespan pattern avoids that.

```python
async with client:
    jwks = await client.get_jwks()
```

Pass `session=` to use your own `aiohttp.ClientSession`; the client then never closes it. A client that is idle can move to another event loop (for example a module-level instance across test loops) and rebuilds its loop-bound state; using it from a second loop while it is busy raises `RuntimeError('OIDCClient is in use on another event loop')`.

### Valkey Cache

Async caching using Valkey (Redis-compatible) with automatic Msgpack serialization for complex types. Configure the connection with `VALKEY_DSN` (or `VALKEY_SENTINEL_DSN` for Sentinel).

```python
from python3_commons import cache

# Store any serializable value (msgspec Structs, Pydantic models, dicts, ...)
await cache.store("user:123", {"name": "Alice", "role": "admin"}, ttl=3600)
user_data = await cache.get("user:123", default={})

# Decode into a typed object
user = await cache.get("user:123", data_type=User)

# Hashes: whole dict or individual fields
await cache.store_dict("settings:123", {"theme": "dark", "page_size": 50}, ttl=3600)
settings = await cache.get_dict("settings:123")
await cache.set_dict_item("settings:123", "theme", "light")
theme = await cache.get_dict_item("settings:123", "theme", default="dark")

# Lists and sets
await cache.store_sequence("recent:123", ["a", "b"], ttl=600)
recent = await cache.get_sequence("recent:123")
await cache.add_set_item("active_users", "user:123")
is_active = await cache.has_set_item("active_users", "user:123")
```

#### `ValkeyCache` (the 1.0 API)

`ValkeyCache` holds one client and exposes the same commands as methods, already under the 1.0 contract: writes
return `bool` and deletes return `int`, collection writes (`store_sequence`, `store_dict`, `store_set`) replace the
key (use `append_sequence` / `update_dict` to merge), a non-positive `ttl` raises `ValueError`, and with
`fail_open=True` (the default) only transient errors (connection, timeout, server loading) are swallowed.

```python
from python3_commons.cache import ValkeyCache, close_default_cache, get_default_cache

async with ValkeyCache.from_settings() as vcache:  # reads VALKEY_* through get_valkey_settings()
    await vcache.store("user:123", {"name": "Alice"}, ttl=3600)

# or one cache per event loop, created on first use and closed in the lifespan
vcache = get_default_cache()
...
await close_default_cache()
```

`VALKEY_SOCKET_TIMEOUT` (60), `VALKEY_SOCKET_CONNECT_TIMEOUT` (10), `VALKEY_HEALTH_CHECK_INTERVAL` (30),
`VALKEY_RETRIES` (5), `VALKEY_MAX_CONNECTIONS` (unbounded; a number gives a blocking pool) and `VALKEY_POOL_TIMEOUT`
(20) tune the connection; `VALKEY_SENTINELS`, `VALKEY_SENTINEL_SERVICE_NAME` (`myprimary`) and
`VALKEY_SENTINEL_PASSWORD` configure Sentinel. The module functions above keep their 0.29.x behaviour until 1.0;
`get_sequence(..., _type=...)` is deprecated in favour of `container=`.

#### Failure Handling

The cache never takes your application down. If Valkey is unreachable or times out, every function logs the error and carries on:

- Reads return their fallback: `get()` and `get_dict_item()` return `default`, `get_sequence()` returns an empty sequence, `has_set_item()` and `exists()` return `False`, and the rest return `None`.
- Writes and deletes do nothing. `store_bytes(..., if_not_set=True)` returns `None`, so treat a falsy result as "not set".

### Structured Logging

A `JSONFormatter` for structured JSON logging and a `filter_maker` utility for level-based log filtering, compatible with standard Python `logging`.

```python
import logging
from python3_commons.log.formatters import JSONFormatter
from python3_commons.log.filters import filter_maker

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
# Filter to process logs up to INFO level only
handler.addFilter(filter_maker("INFO"))
logging.getLogger().addHandler(handler)

logger = logging.getLogger("app")
logger.info("User logged in", extra={"user_id": "abc-123", "ip": "1.2.3.4"})
```

### Serialization

Enhanced JSON and Msgpack serialization for types not handled by default (Decimal, datetime, date, dataclasses, Pydantic models).

```python
from python3_commons.serializers.msgspec import serialize_msgpack, deserialize_msgpack
from decimal import Decimal
from datetime import datetime

data = {
    "amount": Decimal("150.75"),
    "timestamp": datetime.now(),
    "tags": {"finance", "internal"}
}

# Serialize to Msgpack
binary = serialize_msgpack(data)

# Deserialize back
restored = deserialize_msgpack(binary)
```

Without `data_type`, decoding is untyped: the `Decimal` and `datetime` above come back as `str` and the `set` as a
`list`. Pass the type to get the original values back:

```python
from dataclasses import dataclass

@dataclass
class Payment:
    amount: Decimal
    timestamp: datetime
    tags: set[str]

binary = serialize_msgpack(Payment(Decimal("150.75"), datetime.now(), {"finance"}))
payment = deserialize_msgpack(binary, data_type=Payment)  # Decimal, datetime and set again
```

### RBAC Permissions

Database-backed Role-Based Access Control (RBAC) permission checking for users and API keys.

```python
from python3_commons.permissions import has_user_permission, has_api_key_permission
from uuid import UUID

user_uuid = UUID("...")
allowed = await has_user_permission(session, user_uuid, "reports.view")

api_key_uuid = UUID("...")
key_allowed = await has_api_key_permission(session, api_key_uuid, "reports.view")
```

### Configuration Management

Typed application settings powered by Pydantic Settings:

Each settings class has a cached getter (`get_db_settings()` and so on), so importing python3-commons reads no environment variables; call the getters you need in your application lifespan to fail fast on bad settings. The old module attributes (`settings`, `db_settings`, `valkey_settings`, `s3_settings`, `api_client_settings`, `object_storage_settings`) still work with a `DeprecationWarning` and are removed in 1.0. `get_oidc_settings()` returns a shared `OIDCSettings`.

- `CommonSettings` / `get_common_settings()`: General logging level and format.
- `DBSettings` / `get_db_settings()`: PostgreSQL database settings (`DB_` environment prefix, with `DB_PASS` alias for password).
- `S3Settings` / `get_s3_settings()`: legacy S3 settings (`S3_` environment prefix). Nothing in python3-commons reads them to configure object storage: `object-storage-client` reads `S3_*`/`AWS_*` from the environment itself.
- `ObjectStorageSettings` / `get_object_storage_settings()`: Object storage work path (`OBJECT_STORAGE_` environment prefix: `WORK_PATH`, normalised to a URL like `s3://bucket/data`; a value without a scheme becomes a `file://` URL, a blank one `None`) for your code to read and pass as `work_path`; the module functions do not read it.
- `ApiClientSettings` / `get_api_client_settings()`: API client configuration (`API_CLIENT_` environment prefix: `AUDIT_PATH` full object storage path like `s3://bucket/audit`).
- `OIDCSettings`: OpenID Connect configuration (`OIDC_` environment prefix), including the checks of tokens by
  verifiers built on it: `AUDIENCE`, `ISSUER` (expected `iss`, not checked when empty), `LEEWAY` (clock skew for
  `exp` and `nbf`, seconds, default 0), `JWKS_REFRESH_INTERVAL` (min seconds between JWKS refreshes on a token of
  an unknown key, default 60).
- `ValkeySettings` / `get_valkey_settings()`: Valkey/Redis cache configuration (`VALKEY_` environment prefix).

### File System Utilities

File system traversal helpers:

```python
from python3_commons.fs import iter_files
from pathlib import Path

# Recursively iterate files, ignoring hidden directories
for file_path in iter_files(Path("./data"), recursive=True):
    print(file_path)
```

### General Helpers

A collection of useful utility functions:

- `to_snake_case(text)`: Converts strings to snake_case.
- `round_decimal(value, places)`: Rounds `Decimal` values.
- `tries(n)`: An async retry decorator.
- `log_execution_time`: An async decorator to log how long a function takes.
- `date_from_string` / `datetime_from_string`: Flexible date/time parsing.
- `date_range(start, end)`: Generator yielding dates in a range.
- `request_to_curl`: Converts request parameters to a `curl` command string.
- `SingletonMeta`: Thread-safe singleton metaclass.
- `replace_origin(url, host_url)`: Replaces scheme, host, and port of a URL.
- `parse_string_list`: Parses comma-separated string lists into tuples.

```python
from python3_commons.helpers import tries, log_execution_time, SingletonMeta

@tries(3)
@log_execution_time
async def flaky_network_call():
    ...

class AppService(metaclass=SingletonMeta):
    pass
```

### Async CSV Stream

Efficiently generate CSV data as a byte stream from an async generator of tuples.

```python
from python3_commons.generators import tuple_csv_stream

async def generate_rows():
    for i in range(1000):
        yield (i, f"Name {i}", 10.5 * i)

async for chunk in tuple_csv_stream(generate_rows(), header=("ID", "Name", "Value")):
    # Send chunk to HTTP response or write to file
    pass
```

## Development: running the integration tests

The integration tests (`tests/integration`, marker `integration`) are run locally; there is no CI job for them. The
Valkey and PostgreSQL tests start containers with testcontainers, through podman:

```bash
systemctl --user start podman.socket
REQUIRE_CONTAINERS=1 uv run pytest -m integration --no-cov -p no:cacheprovider
```

- `DOCKER_HOST` defaults to `$XDG_RUNTIME_DIR/podman/podman.sock` when that socket exists; set it to use another
  runtime.
- Without `REQUIRE_CONTAINERS=1` a test whose container cannot start is skipped. With it the test fails, so a green
  run proves the containers ran.
- The encryption and encrypted-audit tests need the `osc` CLI on `PATH` to make a keyring, and are skipped without
  it:
  `CARGO_REGISTRIES_BIXORITY_INDEX="sparse+https://codeberg.org/api/packages/bixority/cargo/" cargo install object-storage-client --registry bixority`.
- `tests/integration/test_auth.py` talks to a real OIDC provider (Cognito) and is skipped unless
  `TEST_OIDC_AUTHORITY_URL` is set (also under `REQUIRE_CONTAINERS=1`). It reads `TEST_OIDC_CLIENT_ID`,
  `TEST_OIDC_CLIENT_SECRET`, `TEST_OIDC_USERNAME` and `TEST_OIDC_PASSWORD`.
