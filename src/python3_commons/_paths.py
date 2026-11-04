# pyright: strict
"""Path and key helpers shared by audit and conf. Stdlib only: conf.py imports this module."""

import os
import re
from urllib.parse import quote, unquote

__all__ = ['AUDIT_SEGMENT_MAX_LENGTH', 'audit_segment', 'clean_key', 'has_scheme', 'normalize_root']

AUDIT_SEGMENT_MAX_LENGTH = 200


_SCHEME_RE = re.compile(r'^([A-Za-z][A-Za-z0-9+.-]*)://')


def has_scheme(value: str) -> bool:
    """Whether `value` starts with a `scheme://` prefix (any scheme, not only the ones the client supports)."""
    return _SCHEME_RE.match(value) is not None


def normalize_root(root: str) -> str:
    """Return a canonical storage root URL: lowercase scheme, no trailing or repeated slashes in the path.

    A root with a scheme (`s3://`, `gs://`, `az://`, `https://host:8443/p`, `file:///x`, ...) keeps its authority
    exactly as written (port, IPv6 host, `container@account` userinfo, case); only the path is cleaned. Unknown
    schemes are left for the object-storage client to reject. A root without a scheme is a local filesystem path:
    `~` is expanded, a relative path is resolved against the current working directory, and the result is returned
    as a percent-encoded `file://` URL (`bucket:prefix` is just a relative path with a colon, kept as is). There is
    no `s3://` default. The result is idempotent.

    Raises `ValueError` for an empty or blank root, and for a root that has nothing but slashes (`/`, `s3://`,
    `file:///`); callers that treat empty as "not configured" map it to `None` first.
    """
    root = root.strip()

    if not root:
        msg = 'storage root is empty'
        raise ValueError(msg)

    match = _SCHEME_RE.match(root)

    if match:
        scheme = match.group(1).lower()
        authority, _, path = root[match.end() :].partition('/')
    else:
        scheme = 'file'
        authority = ''
        # os.path, not Path.resolve(): resolve() follows symlinks, abspath() only normalises the text
        local = os.path.abspath(os.path.expanduser(root))  # noqa: PTH100, PTH111
        path = quote(re.sub(r'/{2,}', '/', local), safe='/:@')

    path = re.sub(r'/{2,}', '/', path).strip('/')

    if not authority and not path:
        msg = 'storage root is empty'
        raise ValueError(msg)

    return f'{scheme}://{authority}/{path}' if path else f'{scheme}://{authority}'


def audit_segment(segment: str) -> str:
    """Make one untrusted value safe as a single object key segment.

    Percent-quotes everything but `-_.~`, replaces `.` and `..` (also when percent-encoded) with `_`
    and caps the length at 200 characters.
    """
    if unquote(segment) in {'.', '..'}:
        return '_'

    return quote(segment, safe='-_.~')[:AUDIT_SEGMENT_MAX_LENGTH]


def clean_key(key: str) -> str:
    """Validate an object key relative to a storage root and return it with slashes collapsed and trimmed.

    Raises `ValueError` for a `://` scheme separator, a NUL, or a `.` or `..` segment. Validation runs before any
    slash is stripped, so `/file://x` is not turned into an absolute URL. The segment check runs on the raw key and
    on its percent-decoded form, because the object-storage client percent-decodes URL paths (`%2e%2e` and, on
    `file://`, `%2F`) and resolves dot segments on `file://`. Segments are split on `/` and on `\\`, because the
    `file://` URL parser treats a backslash as a separator. Backslashes are otherwise allowed. An empty result is
    returned as `''`; callers decide whether that is acceptable.
    """
    if '://' in key:
        msg = 'object key must be relative: it contains a scheme separator'
        raise ValueError(msg)

    for form in (key, unquote(key)):
        if '\x00' in form:
            msg = 'object key contains a NUL character'
            raise ValueError(msg)

        if any(segment in {'.', '..'} for segment in re.split(r'[/\\]', form)):
            msg = 'object key contains a "." or ".." segment'
            raise ValueError(msg)

    return re.sub(r'/{2,}', '/', key).strip('/')
