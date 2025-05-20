import logging
from typing import Mapping

import sqlalchemy as sa
from sqlalchemy import asc, desc, func
from sqlalchemy.sql.elements import BooleanClauseList, UnaryExpression

logger = logging.getLogger(__name__)


def get_query(
    search: Mapping[str, str] | None = None, order_by: str | None = None, columns: Mapping | None = None
) -> tuple[BooleanClauseList, UnaryExpression]:
    """
    :columns:
        Param name ->
        0: Model column
        1: case-insensitive if True
        2: cast value to type
        3: exact match if True, LIKE %value% if False
    """

    order_by_cols = {}

    if order_by:
        for order_by_col in order_by.split(','):
            if order_by_col.startswith('-'):
                direction = desc
                order_by_col = order_by_col[1:]
            else:
                direction = asc

            order_by_cols[order_by_col] = direction

        order_by_clauses = tuple(
            direction(columns[order_by_col][0]) for order_by_col, direction in order_by_cols.items()
        )
    else:
        order_by_clauses = None

    if search:
        where_parts = [
            *(
                (func.upper(columns[k][0]) if columns[k][1] else columns[k][0]) == columns[k][2](v)
                for k, v in search.items()
                if columns[k][3]
            ),
            *(
                (func.upper(columns[k][0]) if columns[k][1] else columns[k][0]).like(f'%{v.upper()}%')
                for k, v in search.items()
                if not columns[k][3]
            ),
        ]
    else:
        where_parts = None

    if where_parts:
        where_clause = sa.and_(*where_parts)
    else:
        where_clause = None

    return where_clause, order_by_clauses
