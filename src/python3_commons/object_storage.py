# pyright: strict
import asyncio
import functools
import io
import logging
import os
import warnings
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Buffer,
    Callable,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import aclosing, asynccontextmanager
from datetime import datetime
from typing import Any, Literal, Protocol, TypedDict, overload
from urllib.parse import urlparse

from python3_commons import _paths
from python3_commons._extras import require

require('object-storage', 'object_storage_client')

from object_storage_client import (
    EncryptedObjectStorageClient,
    EncryptionError,
    Keyring,
    NotEncryptedError,
    ObjectStorageClient,
)

__all__ = [
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
]
logger = logging.getLogger(__name__)
normalize_root = _paths.normalize_root
_KEYRING_ENV = 'OSC_KEYRING_FILE'  # the osc CLI's own variable; the Python binding does not read it
_override: ObjectStorageClient | EncryptedObjectStorageClient | None = None


def _load_keyring(path: str) -> Keyring:
    return Keyring.from_file(path)


def _build_client(profile: str | None = None) -> ObjectStorageClient | EncryptedObjectStorageClient:
    """A plain client, or an encrypting one when `OSC_KEYRING_FILE` is set; read here and never at import.

    `profile` is passed to `ObjectStorageClient(profile=)` as is; python3-commons has no profile setting.
    """
    path = os.environ.get(_KEYRING_ENV, '')
    client = ObjectStorageClient(profile=profile) if profile else ObjectStorageClient()

    if not path.strip():
        return client

    try:
        keyring = _load_keyring(path)
    except EncryptionError as e:
        e.add_note(f'{_KEYRING_ENV}={path!r}')

        raise

    return client.with_encryption(keyring)


@functools.cache
def default_client() -> ObjectStorageClient | EncryptedObjectStorageClient:
    """The process-wide client. With `OSC_KEYRING_FILE` set it encrypts everything python3-commons writes.

    Built on first use and cached. A keyring that cannot be loaded raises `EncryptionError` and is not cached, so the
    next call retries. `default_client.cache_clear()` drops the cached client.
    """
    return _override if _override is not None else _build_client()


def get_client() -> ObjectStorageClient | EncryptedObjectStorageClient:
    """Alias of `default_client()`."""
    return default_client()


def set_default_client(client: ObjectStorageClient | EncryptedObjectStorageClient | None) -> None:
    """Use `client` as the default client, for tests; `None` goes back to building one on first use.

    The module functions, `ObjectStorage` without its own client, and audit writes use it.
    """
    global _override  # noqa: PLW0603

    _override = client
    default_client.cache_clear()


def _plain_view(client: ObjectStorageClient | EncryptedObjectStorageClient) -> ObjectStorageClient | None:
    """The plain view of an encrypting client, `None` for a plain one."""
    return client.without_encryption() if isinstance(client, EncryptedObjectStorageClient) else None


def _add_url_note(error: Exception, url: str) -> None:
    """Attach the object URL to an error that is re-raised; the layer that handles it logs it (convention 7)."""
    error.add_note(f'object storage url: {url}')


def _warn_plaintext(url: str) -> None:
    logger.warning('object storage: %s is not encrypted; read as plaintext', url)


class SupportsNext(Protocol):
    """The slice of `ByteStream` that `ObjectStorageStream` uses."""

    async def next(self) -> bytes: ...

    async def aclose(self) -> None: ...


class ObjectStorageStream:
    """Async chunk stream. With a `plain` view (the client encrypts), a `NotEncryptedError` on the very first read
    reopens `url` through the plain view and returns its first chunk; later reads never fall back.
    """

    def __init__(
        self, stream: SupportsNext, *, url: str | None = None, plain: ObjectStorageClient | None = None
    ) -> None:
        self._stream = stream
        self._url = url
        self._plain = plain if url is not None else None  # cleared once the first read has completed

    async def aclose(self) -> None:
        await self._stream.aclose()

    async def read(self) -> bytes:
        return b''.join([chunk async for chunk in self])

    async def next(self) -> bytes:
        plain = self._plain

        if plain is None:
            return await self._stream.next()

        try:
            chunk = await self._stream.next()
        except NotEncryptedError:
            self._plain = None
            chunk = await self._read_plain(plain)
        except Exception:
            self._plain = None

            raise
        else:
            self._plain = None

        return chunk

    async def _read_plain(self, plain: ObjectStorageClient) -> bytes:
        assert self._url is not None  # noqa: S101

        _warn_plaintext(self._url)
        await self._stream.aclose()
        self._stream = await plain.get_object_stream(self._url)

        return await self._stream.next()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        chunk = await self.next()

        if not chunk:
            raise StopAsyncIteration

        return chunk


def _legacy_join(base: str, path: str) -> str:
    """The pre-0.29.x join, including the dedup heuristic. `path` is already cleaned and non-empty."""
    base_no_scheme = base.split('://', 1)[-1].lstrip('/')

    if path.startswith(f'{base_no_scheme}/') or path == base_no_scheme:
        prefix = base[: len(base) - len(base_no_scheme)]

        return f'{prefix}{path}'

    parsed = urlparse(base)
    base_path = parsed.path.lstrip('/')

    if base_path and (path.startswith(f'{base_path}/') or path == base_path):
        prefix_url = f'{parsed.scheme}://{parsed.netloc}' if parsed.scheme else ''

        return f'{prefix_url}/{path}'

    return f'{base}/{path}'


def _build_url(work_path: str, path: str, *, allow_absolute: bool, stacklevel: int) -> str:
    if _paths.has_scheme(path):
        if not allow_absolute:
            warnings.warn(
                f'Absolute URLs in the object key are deprecated and raise ValueError in python3-commons 1.0; '
                f'pass allow_absolute=True to opt in: {path}',
                DeprecationWarning,
                stacklevel=stacklevel,
            )

        return path

    key = _paths.clean_key(path)
    base = normalize_root(work_path)

    if not key:
        return base

    plain = f'{base}/{key}'
    legacy = _legacy_join(base, key)

    if legacy != plain:
        warnings.warn(
            f'The object-storage prefix deduplication is deprecated and is removed in python3-commons 1.0: '
            f'work_path={work_path!r}, path={path!r} resolves to {legacy} now and to {plain} in 1.0',
            DeprecationWarning,
            stacklevel=stacklevel,
        )

    return legacy


def build_url(work_path: str, path: str = '', *, allow_absolute: bool = False) -> str:
    """Join `work_path` and a relative object key.

    Absolute URLs in `path` and the prefix deduplication are deprecated and removed in 1.0.
    """
    return _build_url(work_path, path, allow_absolute=allow_absolute, stacklevel=3)


def _object_url(work_path: str, path: str, *, allow_absolute: bool) -> str:
    """Resolve the URL of one object. An empty key would address the root itself, so it raises ValueError."""
    if not _paths.has_scheme(path) and not _paths.clean_key(path):
        msg = 'object key is empty'
        raise ValueError(msg)

    return _build_url(work_path, path, allow_absolute=allow_absolute, stacklevel=4)


type _ClientFactory = Callable[[], ObjectStorageClient | EncryptedObjectStorageClient]


async def _put(get: _ClientFactory, url: str, content: Buffer) -> str:
    try:
        await get().put_object(url, content)
        logger.debug('Stored object into object storage: %s', url)
    except Exception as e:
        _add_url_note(e, url)

        raise

    return url


async def _put_stream(get: _ClientFactory, url: str, chunks: AsyncIterable[Buffer], size_hint: int | None) -> str:
    try:
        await get().put_object_stream(url, chunks, size_hint=size_hint)
        logger.debug('Stored object stream into object storage: %s', url)
    except Exception as e:
        _add_url_note(e, url)

        raise

    return url


@asynccontextmanager
async def _open_stream(get: _ClientFactory, url: str) -> AsyncGenerator[ObjectStorageStream]:
    logger.debug('Getting object stream from object storage: %s', url)

    try:
        client = get()
        plain = _plain_view(client)

        try:
            stream = await client.get_object_stream(url)
        except NotEncryptedError:
            if plain is None:
                raise

            _warn_plaintext(url)
            stream = await plain.get_object_stream(url)
            plain = None  # already the plain stream: nothing to fall back to
    except Exception as e:
        _add_url_note(e, url)

        raise

    wrapper = ObjectStorageStream(stream, url=url, plain=plain)

    try:
        yield wrapper
    finally:
        await wrapper.aclose()


async def _read(get: _ClientFactory, url: str) -> bytes:
    logger.debug('Getting object from object storage: %s', url)

    try:
        client = get()

        try:
            body = await client.get_object(url)
        except NotEncryptedError:
            plain = _plain_view(client)

            if plain is None:
                raise

            _warn_plaintext(url)
            body = await plain.get_object(url)
    except Exception as e:
        _add_url_note(e, url)

        raise

    return body


async def _delete(get: _ClientFactory, url: str) -> None:
    logger.debug('Removing object from object storage: %s', url)

    try:
        await get().delete_object(url)
    except Exception as e:
        _add_url_note(e, url)

        raise


async def _delete_prefix(get: _ClientFactory, url: str) -> None:
    try:
        await get().delete_objects_by_prefix(url)
        logger.debug('Removed objects by prefix from object storage: %s', url)
    except Exception as e:
        _add_url_note(e, url)

        raise


async def put_object(  # noqa: PLR0913  # public signature is pinned (C1/C2)
    work_path: str,
    path: str = '',
    data: io.BytesIO | Buffer | None = None,
    length: int | None = None,
    part_size: int | None = None,
    *,
    allow_absolute: bool = False,
) -> str:
    """Store `data` under `path`. `data=None` (an empty object), `length` and `part_size` are deprecated.

    `data` becomes required in python3-commons 1.0; `length` and `part_size` were never used and are removed then.
    """
    if data is None:
        warnings.warn(
            'put_object: data=None is deprecated and data is required in python3-commons 1.0; '
            'pass b"" to store an empty object',
            DeprecationWarning,
            stacklevel=2,
        )

    if length is not None or part_size is not None:
        warnings.warn(
            'put_object: the length and part_size parameters are ignored and are removed in python3-commons 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

    content = b'' if data is None else data.getvalue() if isinstance(data, io.BytesIO) else data
    url = _object_url(work_path, path, allow_absolute=allow_absolute)

    return await _put(get_client, url, content)


async def put_object_stream(
    work_path: str,
    path: str,
    chunks: AsyncIterable[Buffer],
    *,
    size_hint: int | None = None,
    allow_absolute: bool = False,
) -> str:
    """Store an async iterable of chunks as one object, through the same client as `put_object`.

    The object is created only if the iterable finishes cleanly. `size_hint` is the expected total size, if known.
    """
    url = _object_url(work_path, path, allow_absolute=allow_absolute)

    return await _put_stream(get_client, url, chunks, size_hint)


@asynccontextmanager
async def get_object_stream(
    work_path: str, path: str = '', *, allow_absolute: bool = False
) -> AsyncGenerator[ObjectStorageStream]:
    url = _object_url(work_path, path, allow_absolute=allow_absolute)

    async with _open_stream(get_client, url) as stream:
        yield stream


async def get_object(work_path: str, path: str = '', *, allow_absolute: bool = False) -> bytes:
    url = _object_url(work_path, path, allow_absolute=allow_absolute)

    return await _read(get_client, url)


_METADATA_CONCURRENCY = 16
_LEGACY_LISTING_WARNING = (
    'list_objects: in 1.0 missing objects are skipped and unavailable metadata is None, with aware datetimes only'
)


class ObjectKey(TypedDict):
    Key: str


class ObjectInfo(ObjectKey):
    LastModified: datetime
    Size: int
    ETag: str | None


async def _object_info(
    client: ObjectStorageClient | EncryptedObjectStorageClient, obj_url: str, key: str, semaphore: asyncio.Semaphore
) -> tuple[ObjectInfo, bool]:
    """One listing item and whether the legacy placeholder values were used for it."""
    async with semaphore:
        try:
            meta = await client.get_object_metadata(obj_url)
            raw_modified = meta.get('last_modified')
            modified = datetime.fromisoformat(raw_modified) if raw_modified else None
            size = meta.get('size_bytes', 0)
            etag = meta.get('e_tag')
        except Exception:  # noqa: BLE001  # missing object or unavailable metadata: the legacy placeholders, see list_objects
            return {'Key': key, 'LastModified': datetime.now(), 'Size': 0, 'ETag': None}, True  # noqa: DTZ005  # legacy naive value

    legacy = modified is None

    return {
        'Key': key,
        'LastModified': modified or datetime.now(),  # noqa: DTZ005  # legacy naive value
        'Size': size,
        'ETag': etag,
    }, legacy


async def _list(
    get: _ClientFactory, url: str, prefix_clean: str, *, recursive: bool, with_metadata: bool
) -> AsyncGenerator[ObjectInfo | ObjectKey]:
    """The objects listed under `url`; keys are `prefix_clean` plus the listed name. See `list_objects`."""
    client = get()
    # An encrypting client raises NotEncryptedError for a plaintext object's metadata: ask the plain view (stored size)
    metadata_client = _plain_view(client) or client
    semaphore = asyncio.Semaphore(_METADATA_CONCURRENCY)
    warned = False
    names_stream = await client.list_objects_stream(url)

    try:
        async for batch in names_stream:
            names = [name for name in batch if recursive or '/' not in name]
            keys = [f'{prefix_clean}/{name}' if prefix_clean else name for name in names]

            if not with_metadata:
                items: list[ObjectInfo | ObjectKey] = [{'Key': key} for key in keys]
            else:
                # The TaskGroup is done before anything is yielded (no yield inside a TaskGroup).
                # Item names are relative to the listed URL.
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(_object_info(metadata_client, f'{url.rstrip("/")}/{name}', key, semaphore))
                        for name, key in zip(names, keys, strict=True)
                    ]

                results = [task.result() for task in tasks]
                items = [info for info, _ in results]
                fallbacks = [info['Key'] for info, legacy in results if legacy]

                if fallbacks and not warned:
                    warned = True
                    logger.warning(
                        'object storage: no metadata for %d listed object(s) under %s (first: %s); '
                        'placeholder values returned',
                        len(fallbacks),
                        url,
                        fallbacks[0],
                    )
                    warnings.warn(_LEGACY_LISTING_WARNING, DeprecationWarning, stacklevel=2)

            for item in items:
                yield item
    finally:
        await names_stream.aclose()


@overload
def list_objects(
    work_path: str,
    prefix: str = '',
    *,
    recursive: bool = True,
    allow_absolute: bool = False,
    with_metadata: Literal[True] = True,
) -> AsyncGenerator[ObjectInfo]: ...
@overload
def list_objects(
    work_path: str, prefix: str = '', *, recursive: bool = True, allow_absolute: bool = False, with_metadata: bool
) -> AsyncGenerator[ObjectInfo | ObjectKey]: ...
async def list_objects(
    work_path: str,
    prefix: str = '',
    *,
    recursive: bool = True,
    allow_absolute: bool = False,
    with_metadata: bool = True,
) -> AsyncGenerator[ObjectInfo | ObjectKey]:
    """List the objects under `prefix`, in batches, with `Key`, `LastModified`, `Size` and `ETag`.

    Metadata of a batch is fetched concurrently (at most 16 calls at a time) before any of its items is yielded. With
    `with_metadata=False` no metadata calls are made and items carry only `Key`. When the client encrypts, metadata
    comes from the plain view, so `Size` is the stored (ciphertext) size.

    Deprecated, removed in 1.0: a missing object or failed metadata call still yields `LastModified=datetime.now()`
    (naive) and `Size=0`, and a missing timestamp yields a naive `now()`. Each call that falls back logs one WARNING
    and emits one DeprecationWarning. In 1.0 missing objects are skipped and unavailable metadata is `None`.
    """
    url = _build_url(work_path, prefix, allow_absolute=allow_absolute, stacklevel=3)
    prefix_clean = prefix.strip('/') if _paths.has_scheme(prefix) else _paths.clean_key(prefix)

    async with aclosing(
        _list(get_client, url, prefix_clean, recursive=recursive, with_metadata=with_metadata)
    ) as listing:
        async for item in listing:
            yield item


async def get_object_streams(
    work_path: str, path: str = '', *, recursive: bool = True, allow_absolute: bool = False
) -> AsyncGenerator[tuple[str, datetime, ObjectStorageStream]]:
    """Yield `(key, last_modified, stream)`, each stream open only until the consumer asks for the next item.

    A live stream is yielded from inside its context, so consumers must close the generator, including when they
    break out early: `async with contextlib.aclosing(get_object_streams(...)) as it:`.
    """
    async with aclosing(list_objects(work_path, path, recursive=recursive, allow_absolute=allow_absolute)) as listing:
        async for obj in listing:
            object_name = obj['Key']
            last_modified = obj['LastModified']

            async with get_object_stream(work_path, object_name, allow_absolute=allow_absolute) as stream:
                yield object_name, last_modified, stream


async def get_objects(
    work_path: str, path: str = '', *, recursive: bool = True, allow_absolute: bool = False
) -> AsyncGenerator[tuple[str, datetime, bytes]]:
    """Yield `(key, last_modified, data)`. No stream is open while the consumer runs.

    Wrap the generator in `contextlib.aclosing` when you may stop early: it holds the listing open.
    """
    async with aclosing(list_objects(work_path, path, recursive=recursive, allow_absolute=allow_absolute)) as listing:
        async for obj in listing:
            object_name = obj['Key']

            async with get_object_stream(work_path, object_name, allow_absolute=allow_absolute) as stream:
                data = await stream.read()

            yield object_name, obj['LastModified'], data


_DELETE_CONCURRENCY = 16


async def _remove_named(
    get: _ClientFactory, name: str, resolve: Callable[[str], str], semaphore: asyncio.Semaphore
) -> dict[str, object] | None:
    """Delete one named object; the error dict on failure, `None` on success."""
    try:
        url = resolve(name)
    except ValueError as e:
        return {'Key': name, 'Error': str(e), 'Exception': e}

    async with semaphore:
        try:
            await get().delete_object(url)
        except Exception as e:  # noqa: BLE001  # reported per object in the returned error dict
            _add_url_note(e, url)

            return {'Key': name, 'Error': str(e), 'Exception': e}

    return None


async def _delete_named(
    get: _ClientFactory, names: list[str], resolve: Callable[[str], str], *, raise_errors: bool
) -> list[Mapping[Any, Any]]:
    semaphore = asyncio.Semaphore(_DELETE_CONCURRENCY)

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(_remove_named(get, name, resolve, semaphore)) for name in names]

    errors: list[Mapping[Any, Any]] = [error for task in tasks if (error := task.result()) is not None]

    if errors and raise_errors:
        msg = f'failed to remove {len(errors)} of {len(names)} object(s)'

        raise ExceptionGroup(msg, [error['Exception'] for error in errors])

    return errors


def _removal_plan(prefix: str | None, object_names: Iterable[str] | None) -> tuple[bool, list[str]]:
    """Whether to delete by prefix (which takes precedence), and the names otherwise."""
    names = list(object_names) if object_names else []

    return bool(prefix) or (prefix == '' and not names), names


async def remove_object(work_path: str, path: str = '', *, allow_absolute: bool = False) -> None:
    url = _object_url(work_path, path, allow_absolute=allow_absolute)

    await _delete(get_client, url)


async def remove_objects(  # noqa: PLR0913  # public signature is pinned (C1/C2)
    work_path: str,
    prefix: str | None = None,
    object_names: Iterable[str] | None = None,
    *,
    remove_all: bool = False,
    allow_absolute: bool = False,
    raise_errors: bool = False,
) -> Sequence[Mapping[Any, Any]] | None:
    """Delete by `prefix` (which takes precedence) or by `object_names`.

    A prefix that is empty after normalisation (`''`, `'/'`, `'//'`) would delete everything under `work_path`, so it
    raises `ValueError` unless `remove_all=True`. `prefix=None` with no names deletes nothing. A name that is empty
    or invalid after normalisation is reported in the returned errors and never reaches the client.

    Named objects are deleted concurrently (at most 16 at a time). Each failure is a dict with `Key`, `Error` (the
    message) and `Exception` (the exception, with an `object storage url` note). With `raise_errors=True` the
    failures raise as an `ExceptionGroup` instead of being returned. A prefix failure always raises.
    """
    use_prefix, names = _removal_plan(prefix, object_names)

    if not use_prefix and not names:
        return None

    if use_prefix:
        assert prefix is not None  # noqa: S101

        url = _build_url(work_path, prefix, allow_absolute=allow_absolute, stacklevel=3)

        # The URL check catches the dedup heuristic resolving a non-empty prefix to the root (`data/` under `./data`).
        if not remove_all and (
            (not _paths.has_scheme(prefix) and not _paths.clean_key(prefix))
            or url == _build_url(work_path, '', allow_absolute=False, stacklevel=3)
        ):
            msg = 'refusing to remove objects by a prefix that resolves to the storage root: pass remove_all=True'
            raise ValueError(msg)

        await _delete_prefix(get_client, url)

        return None

    errors = await _delete_named(
        get_client,
        names,
        lambda name: _object_url(work_path, name, allow_absolute=allow_absolute),
        raise_errors=raise_errors,
    )

    return errors or None


class ObjectStorage:
    """Object storage under one root, with an injectable client.

    The root is normalised once with `normalize_root`. Keys are relative to it: an absolute URL, a `.` or `..` segment,
    a NUL or an empty key raise `ValueError`, and there is no prefix deduplication. Reads, listing and errors behave
    like the module functions (plaintext fallback under an encrypting client, error notes, no logging of failures).

    `client` is used as is. Without it the instance uses `default_client()`, or, with `profile`, builds a client with
    `ObjectStorageClient(profile=profile)`, which `OSC_KEYRING_FILE` wraps like the default one. `profile` is a
    pass-through: python3-commons has no profile setting.
    """

    def __init__(
        self,
        root: str,
        *,
        client: ObjectStorageClient | EncryptedObjectStorageClient | None = None,
        profile: str | None = None,
    ) -> None:
        if client is not None and profile:
            msg = 'pass either client or profile, not both'
            raise ValueError(msg)

        self._root = normalize_root(root)

        if client is not None:
            self._client = client
        elif profile:
            self._client = _build_client(profile=profile)
        else:
            self._client = default_client()

    @property
    def root(self) -> str:
        return self._root

    @property
    def client(self) -> ObjectStorageClient | EncryptedObjectStorageClient:
        return self._client

    def _get(self) -> ObjectStorageClient | EncryptedObjectStorageClient:
        return self._client

    def url(self, path: str = '') -> str:
        """The URL of the object `path`, or of the root for an empty path."""
        key = _paths.clean_key(path)

        return f'{self._root}/{key}' if key else self._root

    def _object_url(self, path: str) -> str:
        if not _paths.clean_key(path):
            msg = 'object key is empty'
            raise ValueError(msg)

        return self.url(path)

    async def put_object(self, path: str, data: io.BytesIO | Buffer) -> str:
        content = data.getvalue() if isinstance(data, io.BytesIO) else data

        return await _put(self._get, self._object_url(path), content)

    async def put_object_stream(self, path: str, chunks: AsyncIterable[Buffer], *, size_hint: int | None = None) -> str:
        return await _put_stream(self._get, self._object_url(path), chunks, size_hint)

    @asynccontextmanager
    async def get_object_stream(self, path: str) -> AsyncGenerator[ObjectStorageStream]:
        url = self._object_url(path)

        async with _open_stream(self._get, url) as stream:
            yield stream

    async def get_object(self, path: str) -> bytes:
        return await _read(self._get, self._object_url(path))

    @overload
    def list_objects(
        self, prefix: str = '', *, recursive: bool = True, with_metadata: Literal[True] = True
    ) -> AsyncGenerator[ObjectInfo]: ...
    @overload
    def list_objects(
        self, prefix: str = '', *, recursive: bool = True, with_metadata: bool
    ) -> AsyncGenerator[ObjectInfo | ObjectKey]: ...
    async def list_objects(
        self, prefix: str = '', *, recursive: bool = True, with_metadata: bool = True
    ) -> AsyncGenerator[ObjectInfo | ObjectKey]:
        """Like the module `list_objects`, including the deprecated placeholder values."""
        url = self.url(prefix)

        async with aclosing(
            _list(self._get, url, _paths.clean_key(prefix), recursive=recursive, with_metadata=with_metadata)
        ) as listing:
            async for item in listing:
                yield item

    async def get_object_streams(
        self, path: str = '', *, recursive: bool = True
    ) -> AsyncGenerator[tuple[str, datetime, ObjectStorageStream]]:
        """Like the module `get_object_streams`: wrap it in `contextlib.aclosing`."""
        async with aclosing(self.list_objects(path, recursive=recursive)) as listing:
            async for obj in listing:
                async with self.get_object_stream(obj['Key']) as stream:
                    yield obj['Key'], obj['LastModified'], stream

    async def get_objects(
        self, path: str = '', *, recursive: bool = True
    ) -> AsyncGenerator[tuple[str, datetime, bytes]]:
        """Like the module `get_objects`: no stream is open while the consumer runs."""
        async with aclosing(self.list_objects(path, recursive=recursive)) as listing:
            async for obj in listing:
                async with self.get_object_stream(obj['Key']) as stream:
                    data = await stream.read()

                yield obj['Key'], obj['LastModified'], data

    async def remove_object(self, path: str) -> None:
        await _delete(self._get, self._object_url(path))

    async def remove_objects(
        self,
        prefix: str | None = None,
        object_names: Iterable[str] | None = None,
        *,
        remove_all: bool = False,
        raise_errors: bool = False,
    ) -> Sequence[Mapping[Any, Any]] | None:
        """Like the module `remove_objects`; an empty prefix needs `remove_all=True`."""
        use_prefix, names = _removal_plan(prefix, object_names)

        if not use_prefix and not names:
            return None

        if use_prefix:
            assert prefix is not None  # noqa: S101

            if not remove_all and not _paths.clean_key(prefix):
                msg = 'refusing to remove objects by a prefix that resolves to the storage root: pass remove_all=True'
                raise ValueError(msg)

            await _delete_prefix(self._get, self.url(prefix))

            return None

        errors = await _delete_named(self._get, names, self._object_url, raise_errors=raise_errors)

        return errors or None
