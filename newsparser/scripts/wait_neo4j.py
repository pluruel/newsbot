# newsparser/scripts/wait_neo4j.py
"""Block until the neo4j bolt port accepts TCP connections.

systemd ExecStartPre= hook: compose's `depends_on: {condition: service_healthy}`
ordering was lost when the dispatcher moved out of docker — on a cold boot the
container needs ~30s before bolt is up, and a unit that starts sooner fails its
first cycle. Exits 0 as soon as the port accepts, 1 after the deadline.
"""
import os
import socket
import sys
import time
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    parsed = urlparse(uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 7687
    deadline = time.monotonic() + int(os.environ.get("NEO4J_WAIT_TIMEOUT", "120"))
    while True:
        try:
            with socket.create_connection((host, port), timeout=3):
                return
        except OSError:
            if time.monotonic() >= deadline:
                print(f"neo4j bolt not reachable at {host}:{port} within deadline",
                      file=sys.stderr)
                sys.exit(1)
            time.sleep(2)


if __name__ == "__main__":
    main()
