"""Runs the synthetic workload against MySQL at a fixed rate.

Each worker thread owns one connection and one pacer. Workers keep trying when
the database refuses them: an injected fault must show up as failed and slow
operations in the metrics, not as a workload that silently stopped.
"""

import logging
import os
import random
import signal
import threading
import time

import pymysql
from prometheus_client import Counter, Histogram, start_http_server

from shop_workload.pacing import RatePacer
from shop_workload.query_mix import DEFAULT_SEED_SIZES, build_query_mix, choose_operation
from shop_workload.settings import load_settings

RECONNECT_DELAY_SECONDS = 1.0
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_SECONDS = 30

OPERATIONS_TOTAL = Counter(
    "workload_operations_total",
    "Workload operations attempted, by statement and outcome.",
    ["operation", "outcome"],
)
OPERATION_DURATION_SECONDS = Histogram(
    "workload_operation_duration_seconds",
    "Time from sending a statement to reading its full result.",
    ["operation"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
CONNECTION_FAILURES_TOTAL = Counter(
    "workload_connection_failures_total",
    "Failed attempts to open a database connection.",
)

logger = logging.getLogger("shop_workload")


def open_connection(settings):
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        autocommit=True,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=STATEMENT_TIMEOUT_SECONDS,
        write_timeout=STATEMENT_TIMEOUT_SECONDS,
    )


def close_quietly(connection):
    if connection is None:
        return
    try:
        connection.close()
    except Exception:  # closing a broken connection must never stop a worker
        pass


def run_worker(worker_index, settings, stop_requested, open_connection=open_connection):
    """Runs operations until stop is requested.

    Any failure, of any exception type, is counted, logged and followed by a
    reconnect. A worker must never exit on its own: a silently dead worker
    would lower the rate while every health check stayed green.
    """
    random_source = random.Random(settings.random_seed + worker_index)
    query_mix = build_query_mix(DEFAULT_SEED_SIZES)
    pacer = RatePacer(settings.operations_per_second / settings.worker_count)
    connection = None

    while not stop_requested.is_set():
        if connection is None:
            try:
                connection = open_connection(settings)
            except Exception as connect_error:
                CONNECTION_FAILURES_TOTAL.inc()
                logger.warning("worker %d cannot connect: %s", worker_index, connect_error)
                stop_requested.wait(RECONNECT_DELAY_SECONDS)
                continue

        pacer.wait_for_next_slot()
        operation = choose_operation(query_mix, random_source)
        started_at = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute(operation.sql, operation.parameters)
                cursor.fetchall()
        except Exception as statement_error:
            OPERATIONS_TOTAL.labels(operation.name, "error").inc()
            logger.warning("worker %d %s failed: %s", worker_index, operation.name, statement_error)
            close_quietly(connection)
            connection = None
        else:
            OPERATIONS_TOTAL.labels(operation.name, "ok").inc()
        finally:
            OPERATION_DURATION_SECONDS.labels(operation.name).observe(
                time.monotonic() - started_at
            )

    close_quietly(connection)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(os.environ)
    logger.info("starting workload: %r", settings)

    stop_requested = threading.Event()
    signal.signal(signal.SIGTERM, lambda signal_number, frame: stop_requested.set())
    signal.signal(signal.SIGINT, lambda signal_number, frame: stop_requested.set())

    start_http_server(settings.metrics_port)
    workers = [
        threading.Thread(
            target=run_worker,
            args=(worker_index, settings, stop_requested),
            name=f"workload-worker-{worker_index}",
        )
        for worker_index in range(settings.worker_count)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    logger.info("workload stopped")


if __name__ == "__main__":
    main()
