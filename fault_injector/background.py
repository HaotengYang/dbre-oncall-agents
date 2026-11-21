"""Background application traffic that a fault keeps running.

Several faults need more than one held connection: a stream of clients that
keep doing something. Each client runs in its own thread with its own
connection, as separate application processes would.
"""

import contextlib
import threading


class BackgroundClients:
    """Starts client threads over time and stops them all on request.

    client_body(connection, stop_requested) is the work of one client. It is
    called with an open application connection and should return when
    stop_requested is set. Any exception ends that client quietly: a client
    refused or cut off by the server is part of the fault, not an error here.
    """

    def __init__(self, open_connection, client_body, client_limit, seconds_between_clients):
        self._open_connection = open_connection
        self._client_body = client_body
        self._client_limit = client_limit
        self._seconds_between_clients = seconds_between_clients
        self._stop_requested = threading.Event()
        self._connections = []
        self._connections_lock = threading.Lock()
        self._threads = []
        self._spawner = None

    def start(self):
        self._spawner = threading.Thread(target=self._spawn_clients, daemon=True)
        self._spawner.start()

    def _spawn_clients(self):
        while not self._stop_requested.is_set() and len(self._threads) < self._client_limit:
            client_thread = threading.Thread(target=self._run_one_client, daemon=True)
            self._threads.append(client_thread)
            client_thread.start()
            self._stop_requested.wait(self._seconds_between_clients)

    def _run_one_client(self):
        try:
            connection = self._open_connection()
        except Exception:
            return  # refused: the server is full, which is what the fault is for
        with self._connections_lock:
            self._connections.append(connection)
        with contextlib.suppress(Exception):
            self._client_body(connection, self._stop_requested)

    def open_connection_count(self):
        with self._connections_lock:
            return len(self._connections)

    def stop(self, join_seconds=20):
        """Asks every client to stop, then closes their connections."""
        self._stop_requested.set()
        if self._spawner is not None:
            self._spawner.join(timeout=join_seconds)
        for client_thread in self._threads:
            client_thread.join(timeout=join_seconds)
        with self._connections_lock:
            connections = list(self._connections)
            self._connections = []
        for connection in connections:
            with contextlib.suppress(Exception):
                connection.close()
