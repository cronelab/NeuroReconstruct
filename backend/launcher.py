"""
NeuroReconstruct — standalone launcher
PyInstaller entry point.
"""
import sys
import os

# Must be set before ANY import that touches ITK/SimpleITK so the thread pool
# initializes with one thread — guarantees deterministic coregistration.
os.environ['ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['NUMEXPR_NUM_THREADS'] = '1'
import threading
import webbrowser
import time
import socket

# IMPORTANT: set NEURO_DATA_DIR *before* importing anything that touches
# database.py, so the DB file lands next to the .exe (not in the temp
# extraction dir which is wiped on exit).
if getattr(sys, 'frozen', False):
    EXE_DIR = os.path.dirname(sys.executable)
    os.environ.setdefault('NEURO_DATA_DIR', EXE_DIR)
    os.chdir(EXE_DIR)
    os.makedirs(os.path.join(EXE_DIR, 'data'), exist_ok=True)

from main import app

import uvicorn


DEFAULT_PORT = 8000
PORT_PROBE_LIMIT = 20


def _is_port_free(port: int) -> bool:
    """True if we can bind 127.0.0.1:port right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def _resolve_port() -> int:
    """
    An explicit NEURO_PORT is a deliberate choice, so honour it as-is and let
    uvicorn report the failure if it is taken. With no override, probe upward
    from the default: a double-clicked exe should still start when something
    else already holds 8000 rather than dying on a raw socket traceback.
    """
    requested = os.environ.get('NEURO_PORT')
    if requested:
        try:
            return int(requested)
        except ValueError:
            print(f"  Invalid NEURO_PORT={requested!r}; falling back to auto-select.")

    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_PROBE_LIMIT):
        if _is_port_free(port):
            if port != DEFAULT_PORT:
                print(f"  Port {DEFAULT_PORT} is in use; starting on {port} instead.")
            return port

    # Nothing free in the range — fall through and let uvicorn raise, so the
    # message names a port the user can actually investigate.
    print(f"  No free port in {DEFAULT_PORT}-{DEFAULT_PORT + PORT_PROBE_LIMIT - 1}.")
    return DEFAULT_PORT


PORT = _resolve_port()
URL = f'http://127.0.0.1:{PORT}'


def _open_browser():
    time.sleep(3)
    webbrowser.open(URL)


if __name__ == '__main__':
    print("=" * 55)
    print("  NeuroReconstruct")
    print(f"  Starting server at {URL} ...")
    print("  Default login:  admin / changeme")
    print("  Close this window to stop the app.")
    print("=" * 55)

    browser_thread = threading.Thread(target=_open_browser, daemon=True)
    browser_thread.start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=PORT,
        log_level="warning",
    )
