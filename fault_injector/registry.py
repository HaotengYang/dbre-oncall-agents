"""The faults the injector knows."""

from fault_injector.faults.cascade import (
    LockWaitExhaustsConnections,
    SlowQueryExhaustsConnections,
)
from fault_injector.faults.connection_exhaustion import GradualLeak, IdleFlood
from fault_injector.faults.lock_wait import BlockedSchemaChange, LongTransaction
from fault_injector.faults.missing_index import DroppedIndex, UnindexedQuery

# The labels a diagnosis can be scored against (docs/DESIGN.md, section 3).
ROOT_CAUSE_LABELS = {"missing_index_slow_query", "lock_wait", "connection_exhaustion"}

_FAULT_CLASSES = (
    DroppedIndex,
    UnindexedQuery,
    LongTransaction,
    BlockedSchemaChange,
    IdleFlood,
    GradualLeak,
    LockWaitExhaustsConnections,
    SlowQueryExhaustsConnections,
)


def list_faults():
    return [fault_class() for fault_class in _FAULT_CLASSES]


def find_fault(fault_id):
    for fault in list_faults():
        if fault.fault_id == fault_id:
            return fault
    available_ids = ", ".join(fault.fault_id for fault in list_faults())
    raise KeyError(f"unknown fault {fault_id!r}. Available: {available_ids}")
