import logging
import os
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_PATH = os.path.join(ROOT_DIR, "backend")
if BACKEND_PATH not in sys.path:
    sys.path.append(BACKEND_PATH)

from polling import run_worker_loop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker_loop()
