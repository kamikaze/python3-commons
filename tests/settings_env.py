"""Hermetic settings for the unit and contract suites, shared by their conftests.

The integration conftest does not use this: the opt-in real-provider tests read the developer's environment.
"""

import os
import sys
from collections.abc import Iterator

import pytest

from python3_commons import conf

# Environment variable prefixes and exact names that feed the settings classes; extend these, not the fixture
SCRUBBED_PREFIXES: tuple[str, ...] = ('DB_', 'S3_', 'VALKEY_', 'OIDC_', 'API_CLIENT_', 'OBJECT_STORAGE_', 'LOGGING_')
# The osc CLI's keyring variables: object_storage reads OSC_KEYRING_FILE, so a developer's own must not leak in
SCRUBBED_NAMES: tuple[str, ...] = ('OSC_KEYRING_FILE', 'OSC_KEYRING_COMMAND')


def scrubbed_names() -> list[str]:
    return [
        name for name in os.environ if name.startswith(SCRUBBED_PREFIXES) or name in SCRUBBED_NAMES
    ]  # a list: the environment is mutated while it is consumed


def clear_settings_caches() -> None:
    """`cache_clear()` on every `get_*` getter of conf, the private `_get_*` ones included."""
    for name, getter in vars(conf).items():
        if name.lstrip('_').startswith('get_') and hasattr(getter, 'cache_clear'):
            getter.cache_clear()


def reset_object_storage_client() -> None:
    """Drop the cached and any injected object-storage client, but only when the module is loaded: do not import it."""
    if module := sys.modules.get('python3_commons.object_storage'):
        module.set_default_client(None)


@pytest.fixture(autouse=True)
def hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run each test without settings-related environment variables and with fresh cached settings."""
    for name in scrubbed_names():
        monkeypatch.delenv(name)

    clear_settings_caches()
    reset_object_storage_client()

    yield

    clear_settings_caches()
    reset_object_storage_client()
