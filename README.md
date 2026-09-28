# python3-commons

Re-usable code for Python 3 projects.

## Installation

```bash
uv add python3-commons
```

### Optional Dependencies

Some features require extra dependencies. You can install them individually or all at once:

- `api-client`: For `python3_commons.api_client`
- `audit`: For `python3_commons.audit`
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

A context manager for `aiohttp` requests with built-in audit logging to S3 and standardized error mapping to Python exceptions.

#### Standardized Exception Mapping

HTTP error status codes and network failures are mapped to built-in Python exceptions:
- `400 Bad Request` -> `ValueError`
- `401 Unauthorized`, `403 Forbidden` -> `PermissionError`
- `404 Not Found` -> `LookupError`
- `429 Too Many Requests` -> `InterruptedError`
- Other 4xx/5xx responses -> `aiohttp.ClientResponseError`
- Connection errors -> `ConnectionRefusedError` / `ConnectionResetError`

#### Enabling Audit Logging

Audit logging captures the outgoing request (formatted as an executable `curl` command) and the incoming response body, writing them asynchronously to Object Storage.

The destination path is resolved as follows:
- If `ApiClientSettings.audit_path` is configured (via `API_CLIENT_AUDIT_PATH`), it is used directly as the audit root for `api_client` (without prepending `"audit/"`):
  `<audit_path>/<YYYY/MM/DD>/<audit_name>/<uri>/<method>_<timestamp>_<request_id>_{request,response}.txt`
- Otherwise, if `ObjectStorageSettings.work_path` is configured (via `OBJECT_STORAGE_WORK_PATH`), it is used as the root with `"audit/"` prepended:
  `<work_path>/audit/<YYYY/MM/DD>/<audit_name>/<uri>/<method>_<timestamp>_<request_id>_{request,response}.txt`
- If neither is configured, the logs default to:
  `audit/<YYYY/MM/DD>/<audit_name>/<uri>/<method>_<timestamp>_<request_id>_{request,response}.txt`

To enable audit logging:
1. **Configure Object Storage & S3 credentials**:
   ```bash
   # API client audit path or Object storage root
   export API_CLIENT_AUDIT_PATH="my-audit-bucket/audit-logs"
   # or export OBJECT_STORAGE_WORK_PATH="my-audit-bucket"

   # S3 credentials
   export S3_ACCESS_KEY_ID="your-access-key-id"
   export S3_SECRET_ACCESS_KEY="your-secret-access-key"
   export S3_REGION="us-east-1"
   # Optional configurations:
   # export S3_ALLOW_HTTP="false"
   # export S3_ADDRESSING_STYLE="virtual"  # or "path"
   # export S3_CERT_VERIFY="true"
   ```
   Alternatively, you can configure them directly in Python:
   ```python
   from python3_commons.conf import api_client_settings, object_storage_settings, s3_settings
   from pydantic import SecretStr

   api_client_settings.audit_path = "my-audit-bucket/audit-logs"
   # or object_storage_settings.work_path = "my-audit-bucket"

   s3_settings.access_key_id = SecretStr("your-access-key-id")
   s3_settings.secret_access_key = SecretStr("your-secret-access-key")
   s3_settings.region = "us-east-1"
   ```

2. **Pass `audit_name` to `request()`**: Provide a non-empty string identifier (such as the target service or operation name) in the `audit_name` argument. If `audit_name` is omitted (default `None`), audit logging is disabled for that call. If S3 credentials are not configured, audit logging is safely bypassed.

#### Usage Example

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

Async SOAP client support for `zeep` with S3 auditing capabilities.

```python
from python3_commons.soap_client import soap_client

async with soap_client("https://example.com/service?wsdl") as client:
    result = await client.service.GetData(id=42)
```

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

Thin wrapper for `object-storage-client` supporting S3, GCS, Azure, and local file storage, configured via `object_storage_settings` (`OBJECT_STORAGE_WORK_PATH`) and `s3_settings` (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`). Storage functions accept a required `work_path` parameter to define the base storage location.

```python
from python3_commons import object_storage
import io

# Upload an object
await object_storage.put_object(
    work_path="my-bucket", 
    path="uploads/file.txt", 
    data=io.BytesIO(b"Hello World"), 
    length=11
)

# Download an object as bytes
content = await object_storage.get_object("my-bucket", "uploads/file.txt")

# Stream an object
async with object_storage.get_object_stream("my-bucket", "uploads/file.txt") as stream:
    data = await stream.read()

# List objects with a prefix
async for obj in object_storage.list_objects("my-bucket", "uploads/"):
    print(obj['Key'])

# Delete single or multiple objects
await object_storage.remove_object("my-bucket", "uploads/file.txt")
await object_storage.remove_objects("my-bucket", object_names=["uploads/file1.txt", "uploads/file2.txt"])
```

### OIDC Authentication

Client for OpenID Connect authentication, supporting configuration fetching, JWKS, and token acquisition.

```python
from python3_commons.auth import OIDCClient
from pydantic import HttpUrl

client = OIDCClient(
    authority_url=HttpUrl("https://auth.example.com/realms/myrealm"),
    client_id="my-app-client",
    client_secret="secret"
)

async with client:
    token_response = await client.fetch_token(username="user", password="password")
    print(token_response.access_token)
```

### Valkey Cache

Async caching using Valkey (Redis-compatible) with automatic Msgpack serialization for complex types.

```python
from python3_commons import cache

# Store a dictionary
await cache.store("user:123", {"name": "Alice", "role": "admin"}, ttl=3600)

# Retrieve it
user_data = await cache.get("user:123")

# Set operations
await cache.add_set_item("active_users", "user:123")
is_active = await cache.has_set_item("active_users", "user:123")
```

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

- `CommonSettings` / `settings`: General logging level and format.
- `DBSettings` / `db_settings`: PostgreSQL database settings (`DB_` environment prefix, with `DB_PASS` alias for password).
- `S3Settings` / `s3_settings`: S3 credentials and connection configuration (`S3_` environment prefix: `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`).
- `ObjectStorageSettings` / `object_storage_settings`: Object storage root configuration (`OBJECT_STORAGE_` environment prefix: `WORK_PATH`).
- `ApiClientSettings` / `api_client_settings`: API client configuration (`API_CLIENT_` environment prefix: `AUDIT_PATH`).
- `OIDCSettings`: OpenID Connect configuration (`OIDC_` environment prefix).
- `ValkeySettings` / `valkey_settings`: Valkey/Redis cache configuration (`VALKEY_` environment prefix).

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
