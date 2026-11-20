"""Missing-index faults."""

from fault_injector.baseline import BASELINE_INDEXES, drop_index_sql
from fault_injector.faults.base import Fault


class DroppedIndex(Fault):
    """Drops the index behind the workload's "recent orders for a customer"
    query. A quarter of all traffic turns from an index lookup into a scan of
    the whole orders table. No extra traffic is added: the application's own
    queries become the problem, as when an index is removed by a bad release.
    """

    fault_id = "missing_index.dropped_index"
    root_cause = "missing_index_slow_query"
    summary = "Drop the (customer_id, created_at) index on orders."
    dropped_index_name = "idx_orders_customer_created"
    planned_changes = ("drop index idx_orders_customer_created on shop.orders",)

    def start(self, lab_access):
        definition = next(
            definition for definition in BASELINE_INDEXES
            if definition.name == self.dropped_index_name
        )
        with lab_access.control_connection.cursor() as cursor:
            cursor.execute(drop_index_sql(definition))

    def stop(self, lab_access):
        # The index is recreated by the baseline restore that follows every fault.
        pass
