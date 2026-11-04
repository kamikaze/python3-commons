# pyright: strict
"""Library ORM models.

Importing any model module, or calling `load_models()`, registers every library table in `python3_commons.db.metadata`
with all foreign keys resolved. Alembic `env.py` should call `load_models()` and use the returned metadata as
`target_metadata`.
"""

try:
    from sqlalchemy import MetaData
    from sqlalchemy.orm import configure_mappers
except ImportError as e:
    msg = 'Install python3-commons[database] to use this feature'
    raise RuntimeError(msg) from e

from python3_commons.db import metadata
from python3_commons.db.models.auth import ApiKey
from python3_commons.db.models.rbac import RBACApiKeyRole, RBACPermission, RBACRole, RBACRolePermission, RBACUserRole
from python3_commons.db.models.users import User, UserGroup

__all__ = (
    'ApiKey',
    'RBACApiKeyRole',
    'RBACPermission',
    'RBACRole',
    'RBACRolePermission',
    'RBACUserRole',
    'User',
    'UserGroup',
    'load_models',
)

_MODELS = (ApiKey, RBACApiKeyRole, RBACPermission, RBACRole, RBACRolePermission, RBACUserRole, User, UserGroup)


def load_models() -> MetaData:
    """Make sure every library model is registered and return the shared metadata (for Alembic `target_metadata`)."""
    for model in _MODELS:
        model.__table__  # noqa: B018

    configure_mappers()

    return metadata
