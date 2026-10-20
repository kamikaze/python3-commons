from collections.abc import Mapping
from typing import TYPE_CHECKING

try:
    import sqlalchemy as sa
    from sqlalchemy import asc, desc, func
except ImportError as e:
    msg = 'Install python3-commons[database] to use this feature'
    raise RuntimeError(msg) from e

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import ColumnElement, OrderByList, UnaryExpression


def get_query(
    search: Mapping[str, str] | None = None, order_by: str | None = None, columns: Mapping | None = None
) -> tuple[ColumnElement[bool] | None, tuple[UnaryExpression | OrderByList, ...] | None]:
    """
    :columns:
        Param name ->
        0: Model column
        1: case-insensitive if True
        2: cast value to type
        3: exact match if True, LIKE %value% if False
    """

    order_by_clauses = None
    where_clause = None

    if order_by and columns:
        order_by_clauses = tuple(
            desc(columns[col[1:]][0]) if col.startswith('-') else asc(columns[col][0]) for col in order_by.split(',')
        )

    if search and columns:
        where_parts = []

        for k, v in search.items():
            column, case_insensitive, cast, exact = columns[k]
            column = func.upper(column) if case_insensitive else column
            where_parts.append(column == cast(v) if exact else column.like(f'%{v.upper()}%'))

        where_clause = sa.and_(*where_parts)

    return where_clause, order_by_clauses
