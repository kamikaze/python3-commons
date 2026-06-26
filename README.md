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

```python
from python3_commons.api_client import request
from aiohttp import ClientSession

async with ClientSession() as session:
    async with request(
        session, 
        base_url="https://api.example.com", 
        uri="/data", 
        method="get",
        audit_name="my_service_audit"
    ) as response:
        data = await response.json()
```

### SOAP Client

Async SOAP client support for `zeep` with S3 auditing capabilities.

```python
from python3_commons.soap_client import soap_client

async with soap_client("https://example.com/service?wsdl") as client:
    result = await client.service.GetData(id=42)
```

### Database Management

SQLAlchemy async engine and session management with pool tuning, health checks, and dynamic query builders.

```python
from python3_commons.db import AsyncSessionManager
from python3_commons.conf import DBSettings

# Configuration
configs = {"default": DBSettings(dsn="postgresql+asyncpg://user:pass@localhost/db")}
manager = AsyncSessionManager(configs)

# Usage
async with manager.get_session_context("default") as session:
    result = await session.execute(...)

# Health check
from python3_commons.db import is_healthy
await is_healthy(manager.get_engine("default"))
```

### Object Storage

Utilities for async S3 operations using `aiobotocore`.

```python
from python3_commons import object_storage
import io

# Upload an object
await object_storage.put_object(
    bucket_name="my-bucket", 
    path="uploads/file.txt", 
    data=io.BytesIO(b"Hello World"), 
    length=11
)

# Download an object
content = await object_storage.get_object("my-bucket", "uploads/file.txt")

# List objects
async for obj in object_storage.list_objects("my-bucket", "uploads/"):
    print(obj['Key'])
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

A `JSONFormatter` for structured logging, compatible with standard Python `logging`.

```python
import logging
from python3_commons.log.formatters import JSONFormatter

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
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

Database-backed Role-Based Access Control (RBAC) permission checking.

```python
from python3_commons.permissions import has_user_permission
from uuid import UUID

user_uuid = UUID("...")
allowed = await has_user_permission(session, user_uuid, "reports.view")
```

### General Helpers

A collection of useful utility functions:

- `to_snake_case(text)`: Converts strings to snake_case.
- `round_decimal(value, places)`: Rounds `Decimal` values.
- `tries(n)`: An async retry decorator.
- `log_execution_time`: An async decorator to log how long a function takes.
- `date_from_string` / `datetime_from_string`: Flexible date/time parsing.
- `request_to_curl`: Converts request parameters to a `curl` command string.

```python
from python3_commons.helpers import tries, log_execution_time

@tries(3)
@log_execution_time
async def flaky_network_call():
    ...
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
