"""The faults and other scenarios the injector knows."""

from fault_injector.faults.adversarial import LockWaitWithHostileComment
from fault_injector.faults.cascade import (
    LockWaitExhaustsConnections,
    SlowQueryExhaustsConnections,
)
from fault_injector.faults.connection_exhaustion import GradualLeak, IdleFlood
from fault_injector.faults.healthy import HEALTHY_LABEL, HighLoad, SlowReport
from fault_injector.faults.holdout import (
    ActiveSlowCalls,
    ProductsRowLock,
    SteadyIdlePool,
    UnindexedUpdateBlocksWriters,
)
from fault_injector.faults.lock_wait import BlockedSchemaChange, LongTransaction
from fault_injector.faults.missing_index import DroppedIndex, UnindexedQuery

# The labels a diagnosis can be scored against (docs/DESIGN.md, section 3).
ROOT_CAUSE_LABELS = {"missing_index_slow_query", "lock_wait", "connection_exhaustion"}
# Scenarios that are not faults carry "healthy".
SCENARIO_LABELS = ROOT_CAUSE_LABELS | {HEALTHY_LABEL}

_FAULT_CLASSES = (
    DroppedIndex,
    UnindexedQuery,
    LongTransaction,
    BlockedSchemaChange,
    IdleFlood,
    GradualLeak,
    LockWaitExhaustsConnections,
    SlowQueryExhaustsConnections,
    HighLoad,
    SlowReport,
    LockWaitWithHostileComment,
    # Holdout scenarios (docs/proposals/holdout-cases.md).
    ActiveSlowCalls,
    UnindexedUpdateBlocksWriters,
    ProductsRowLock,
    SteadyIdlePool,
)


def list_faults():
    return [fault_class() for fault_class in _FAULT_CLASSES]


def find_fault(fault_id):
    for fault in list_faults():
        if fault.fault_id == fault_id:
            return fault
    available_ids = ", ".join(fault.fault_id for fault in list_faults())
    raise KeyError(f"unknown fault {fault_id!r}. Available: {available_ids}")
