"""What the gate accepts: exactly one shape of change.

A request is a table, one to four columns and an optional index name. It is
validated against the live schema, and the gate renders the only statement it
will ever run on shop from the validated parts. Text the gate was given is
never executed. Rejection reasons are fixed strings that never quote the
input.
"""

import re
from dataclasses import dataclass
from typing import Optional

SCHEMA_NAME = "shop"
MAX_COLUMNS = 4
PLAIN_NAME = re.compile(r"\A[a-z][a-z0-9_]{0,63}\Z")
_PLAIN_NAME = PLAIN_NAME
_INDEX_NAME = re.compile(r"\Aidx_[a-z0-9_]{1,60}\Z")
# The one statement shape accepted as text. Plain names only: no backticks,
# no prefix lengths, no DESC, no expressions, no UNIQUE/FULLTEXT/SPATIAL, no
# other schema, nothing after the optional clauses but one semicolon.
_STATEMENT = re.compile(
    r"\A\s*CREATE\s+INDEX\s+(?P<name>[A-Za-z0-9_]+)\s+ON\s+(?:shop\s*\.\s*)?(?P<table>[A-Za-z0-9_]+)"
    r"\s*\(\s*(?P<columns>[A-Za-z0-9_]+(?:\s*,\s*[A-Za-z0-9_]+)*)\s*\)"
    r"(?:\s+ALGORITHM\s*=\s*INPLACE)?(?:\s+LOCK\s*=\s*NONE)?\s*;?\s*\Z",
    re.IGNORECASE,
)


class RejectedChange(Exception):
    """The request is not the one allowed shape. The message is fixed text."""


@dataclass(frozen=True)
class TableSchema:
    columns: tuple
    indexes: dict  # index name -> tuple of columns in order


@dataclass(frozen=True)
class IndexRequest:
    table: str
    columns: tuple
    index_name: Optional[str] = None


@dataclass(frozen=True)
class AcceptedChange:
    """A request that passed every check. Only these are ever rendered."""

    table: str
    columns: tuple
    index_name: str


def parse_index_statement(statement_text):
    """Turns one exact CREATE INDEX statement into a request, or refuses."""
    if not isinstance(statement_text, str) or not statement_text.isascii():
        raise RejectedChange("only a plain ASCII CREATE INDEX statement is accepted")
    match = _STATEMENT.match(statement_text)
    if match is None:
        raise RejectedChange(
            "only 'CREATE INDEX idx_name ON shop.table (column, ...)' is accepted"
        )
    columns = tuple(column.strip() for column in match.group("columns").split(","))
    return IndexRequest(table=match.group("table"), columns=columns, index_name=match.group("name"))


def validate_request(request, schema):
    """Checks a request against the live schema {table: TableSchema}."""
    table = request.table
    if not isinstance(table, str) or not _PLAIN_NAME.match(table) or table not in schema:
        raise RejectedChange("the table is not a plain name of an existing shop table")
    table_schema = schema[table]
    columns = tuple(request.columns)
    if not 1 <= len(columns) <= MAX_COLUMNS:
        raise RejectedChange("an index needs one to four columns")
    for column in columns:
        if not isinstance(column, str) or not _PLAIN_NAME.match(column) \
                or column not in table_schema.columns:
            raise RejectedChange("a column is not a plain name of an existing column of the table")
    if len(set(columns)) != len(columns):
        raise RejectedChange("a column is listed twice")

    index_name = request.index_name
    if index_name is None:
        index_name = ("idx_" + table + "_" + "_".join(columns))[:64]
    if not isinstance(index_name, str) or not _INDEX_NAME.match(index_name):
        raise RejectedChange(
            "the index name must be idx_ followed by lower-case letters, digits or "
            "underscores, at most 64 characters"
        )
    if index_name in table_schema.indexes:
        raise RejectedChange("an index with that name already exists on the table")
    for existing_columns in table_schema.indexes.values():
        if tuple(existing_columns[: len(columns)]) == columns:
            raise RejectedChange(
                "redundant: an existing index already starts with these columns in this order"
            )
    return AcceptedChange(table=table, columns=columns, index_name=index_name)


def _check_renderable(accepted):
    """Re-checks every name, so even a hand-built AcceptedChange can only ever
    render plain identifiers."""
    if not isinstance(accepted, AcceptedChange):
        raise TypeError("only an AcceptedChange can be rendered")
    if not _PLAIN_NAME.match(accepted.table) or not _INDEX_NAME.match(accepted.index_name):
        raise RejectedChange("the table or index name is not a plain name")
    if not 1 <= len(accepted.columns) <= MAX_COLUMNS or not all(
        isinstance(column, str) and _PLAIN_NAME.match(column) for column in accepted.columns
    ):
        raise RejectedChange("an index needs one to four plain column names")


def render_create(accepted):
    _check_renderable(accepted)
    return (
        f"CREATE INDEX {accepted.index_name} ON {SCHEMA_NAME}.{accepted.table} "
        f"({', '.join(accepted.columns)}) ALGORITHM = INPLACE LOCK = NONE"
    )


def render_rollback(accepted):
    if not isinstance(accepted, AcceptedChange):
        raise TypeError("only an AcceptedChange can be rendered")
    if not _PLAIN_NAME.match(accepted.table) or not _INDEX_NAME.match(accepted.index_name):
        raise RejectedChange("the table or index name is not a plain name")
    return f"DROP INDEX {accepted.index_name} ON {SCHEMA_NAME}.{accepted.table}"


def render_shadow_create(accepted, shadow_schema):
    """The same statement against the shadow copy of the table."""
    _check_renderable(accepted)
    if not _PLAIN_NAME.match(shadow_schema):
        raise ValueError("the shadow schema must be a plain name")
    return (
        f"CREATE INDEX {accepted.index_name} ON {shadow_schema}.{accepted.table} "
        f"({', '.join(accepted.columns)}) ALGORITHM = INPLACE LOCK = NONE"
    )
