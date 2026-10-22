"""Reload ARQ on source edits; exit if ARQ exits so Docker can restart it."""

import logging
import os
import signal
import subprocess
import sys

from watchfiles import PythonFilter, watch

logger = logging.getLogger(__name__)


def stop_worker(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        logger.warning("Worker did not stop gracefully; terminating its process group")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_worker(command: list[str], paths: list[str]) -> int:
    def request_stop(signum, frame):
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, request_stop)
    process = subprocess.Popen(command, start_new_session=True)
    try:
        for changes in watch(*paths, watch_filter=PythonFilter(), rust_timeout=1000, yield_on_timeout=True):
            code = process.poll()
            if code is not None:
                logger.error("Worker exited with code %s; exiting reloader for Docker restart", code)
                return code if code > 0 else 1
            if changes:
                logger.info("Python source changed; restarting worker")
                stop_worker(process)
                process = subprocess.Popen(command, start_new_session=True)
    except KeyboardInterrupt:
        return 0
    finally:
        stop_worker(process)
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    arq_cmd = [sys.executable, "-m", "arq", "server.worker_main.WorkerSettings"]
    sys.exit(run_worker(arq_cmd, ["/app/server", "/app/package"]))
