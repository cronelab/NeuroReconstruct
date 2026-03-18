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


def _open_browser():
    time.sleep(3)
    webbrowser.open('http://127.0.0.1:8000')


if __name__ == '__main__':
    print("=" * 55)
    print("  NeuroReconstruct")
    print("  Starting server at http://127.0.0.1:8000 ...")
    print("  Default login:  admin / changeme")
    print("  Close this window to stop the app.")
    print("=" * 55)

    browser_thread = threading.Thread(target=_open_browser, daemon=True)
    browser_thread.start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )
