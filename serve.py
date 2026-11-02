"""Start the dashboard server.

Usage:
    python serve.py              (or: make dashboard)

Serves on port 8000 by default. If that port is taken, the next free port is
used instead and the actual URL is printed. Set PORT or HOST to override.

If the database has not been built yet, the pipeline is run first so the
dashboard always has data to show.
"""

import os
import socket
import subprocess
import sys

import uvicorn

from teiko.config import DB_PATH, ROOT_DIR

DEFAULT_PORT = 8000
MAX_PORT_ATTEMPTS = 50


def port_is_free(host: str, port: int) -> bool:
    """True if nothing is currently listening on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Match uvicorn's own socket option. Without it, a port left in TIME_WAIT
        # by a recently stopped server looks busy even though uvicorn can use it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        return True


def find_free_port(host: str, start: int) -> int:
    """First free port at or above `start`."""
    for port in range(start, start + MAX_PORT_ATTEMPTS):
        if port_is_free(host, port):
            return port
    raise RuntimeError(f"No free port found in {start}-{start + MAX_PORT_ATTEMPTS - 1}")


def ensure_database() -> None:
    """Build the database and outputs if `make pipeline` has not been run."""
    if DB_PATH.exists():
        return
    print(f"{DB_PATH.name} not found; running the pipeline first...", flush=True)
    for script in ("load_data.py", "run_analysis.py"):
        subprocess.run([sys.executable, str(ROOT_DIR / script)], check=True)


def main() -> None:
    ensure_database()

    host = os.environ.get("HOST", "127.0.0.1")
    requested = int(os.environ.get("PORT", DEFAULT_PORT))
    port = find_free_port(host, requested)
    if port != requested:
        print(f"Port {requested} is in use; using {port} instead.", flush=True)

    print(
        f"\n  Dashboard: http://localhost:{port}\n",
        flush=True,  # show the URL immediately, even when output is piped
    )
    uvicorn.run("api.app:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
