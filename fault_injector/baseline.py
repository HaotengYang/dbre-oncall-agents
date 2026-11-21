"""The lab's baseline secondary indexes, and how to restore them.

"clear" works from this definition, not from a memory of what was injected, so
it restores the lab even after the injector process was killed.

Diagnosis-side code must not read this file: it states which indexes ought to
exist, which is the answer to a missing-index fault.
"""

from dataclasses import dataclass

SCHEMA_NAME = "shop"
# Indexes the injector itself creates carry this prefix, so the restore can
# remove them without touching anything else.
INJECTOR_INDEX_PREFIX = "idx_fault_"
DDL_LOCK_WAIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class IndexDefinition:
    table: str
    name: str
    columns: tuple
    unique: bool


# Keep in sync with lab/mysql/init/01-schema.sql; a unit test compares them.
BASELINE_INDEXES = (
    IndexDefinition("customers", "uq_customers_email", ("email",), True),
    IndexDefinition("products", "uq_products_sku", ("sku",), True),
    IndexDefinition("orders", "idx_orders_customer_created", ("customer_id", "created_at"), False),
    IndexDefinition("order_items", "idx_order_items_order", ("order_id",), False),
)


def read_live_indexes(connection):
    """Returns the secondary indexes that exist in the shop schema right now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME "
            "FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = %s AND INDEX_NAME <> 'PRIMARY' "
            "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
            (SCHEMA_NAME,),
        )
        statistics_rows = cursor.fetchall()

    columns_by_index = {}
    for table_name, index_name, non_unique, column_name in statistics_rows:
        index_key = (table_name, index_name, int(non_unique) == 0)
        columns_by_index.setdefault(index_key, []).append(column_name)
    return {
        IndexDefinition(table_name, index_name, tuple(columns), unique)
        for (table_name, index_name, unique), columns in columns_by_index.items()
    }


def find_missing_indexes(live_indexes):
    return [definition for definition in BASELINE_INDEXES if definition not in live_indexes]


def find_unexpected_indexes(live_indexes):
    baseline = set(BASELINE_INDEXES)
    return sorted(
        (definition for definition in live_indexes if definition not in baseline),
        key=lambda definition: (definition.table, definition.name),
    )


def create_index_sql(definition):
    unique_keyword = "UNIQUE " if definition.unique else ""
    column_list = ", ".join(definition.columns)
    return (
        f"CREATE {unique_keyword}INDEX {definition.name} "
        f"ON {SCHEMA_NAME}.{definition.table} ({column_list}) "
        "ALGORITHM = INPLACE LOCK = NONE"
    )


def drop_index_sql(definition):
    return f"DROP INDEX {definition.name} ON {SCHEMA_NAME}.{definition.table}"


def end_orphaned_schema_sessions(connection, wait_seconds=10):
    """Ends schema changes left running by an injector that died.

    Only sessions of this account whose current statement creates an
    injector-owned index (INJECTOR_INDEX_PREFIX) are ended. Idle sessions and
    other statements are left alone, so a concurrent "status" command or
    another control connection is not cut off. Ended first, an orphaned schema
    change cannot create its index after the baseline has been restored. An
    account may always end its own sessions; no extra privilege is involved.
    """
    import time

    def orphaned_session_ids():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ID FROM information_schema.PROCESSLIST "
                "WHERE USER = SUBSTRING_INDEX(CURRENT_USER(), '@', 1) "
                "AND ID <> CONNECTION_ID() "
                "AND INFO LIKE %s",
                (f"CREATE INDEX {INJECTOR_INDEX_PREFIX}%",),
            )
            return [row[0] for row in cursor.fetchall()]

    ended_session_ids = orphaned_session_ids()
    with connection.cursor() as cursor:
        for session_id in ended_session_ids:
            try:
                cursor.execute(f"KILL {int(session_id)}")
            except Exception:
                pass  # it ended by itself in the meantime
    deadline = time.monotonic() + wait_seconds
    while orphaned_session_ids() and time.monotonic() < deadline:
        time.sleep(0.2)
    return ended_session_ids


def restore_baseline(connection):
    """Recreates every baseline index that is missing. Returns their names.

    An index with a baseline name but the wrong columns is dropped first, and
    so is any index the injector created (INJECTOR_INDEX_PREFIX). Other
    unexpected indexes are left alone: they are not the injector's.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"SET SESSION lock_wait_timeout = {DDL_LOCK_WAIT_TIMEOUT_SECONDS}"
        )
    live_indexes = read_live_indexes(connection)
    baseline_names = {(definition.table, definition.name) for definition in BASELINE_INDEXES}
    restored_names = []
    with connection.cursor() as cursor:
        for unexpected in find_unexpected_indexes(live_indexes):
            is_injector_owned = unexpected.name.startswith(INJECTOR_INDEX_PREFIX)
            if is_injector_owned or (unexpected.table, unexpected.name) in baseline_names:
                cursor.execute(drop_index_sql(unexpected))
        for missing in find_missing_indexes(live_indexes):
            cursor.execute(create_index_sql(missing))
            restored_names.append(missing.name)
    return restored_names
