"""The dev reloader must exit with its worker, while preserving code reloads."""

import os
import signal
import subprocess
import sys
import time

import pytest


def launch_supervisor(tmp_path, worker_code):
    worker = tmp_path / "worker.py"
    worker.write_text(worker_code, encoding="utf-8")
    code = (
        "import sys; from server.worker_dev import run_worker; "
        f"sys.exit(run_worker([sys.executable, {str(worker)!r}], [{str(tmp_path)!r}]))"
    )
    return subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@pytest.mark.parametrize("delay", [0, 0.5])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_worker_exit_without_file_changes_exits_supervisor(tmp_path, exit_code, delay):
    process = launch_supervisor(tmp_path, f"import time; time.sleep({delay}); raise SystemExit({exit_code})\n")
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == (exit_code or 1), (stdout, stderr)
        assert "Worker exited" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_reload_stops_old_worker_and_shutdown_stops_replacement(tmp_path):
    ready = tmp_path / "pids.txt"
    process = launch_supervisor(
        tmp_path,
        "import os, time\n"
        "from pathlib import Path\n"
        f"with Path({str(ready)!r}).open('a') as file: file.write(str(os.getpid()) + '\\n')\n"
        "while True: time.sleep(0.1)\n",
    )
    pids = []
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pids = ready.read_text().splitlines() if ready.exists() else []
            if pids:
                break
            time.sleep(0.05)
        assert len(pids) == 1
        # Let the watcher attach before making a source edit.
        time.sleep(0.5)
        (tmp_path / "reload.py").write_text("# source changed\n")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pids = ready.read_text().splitlines()
            if len(pids) == 2:
                break
            time.sleep(0.05)
        assert len(pids) == 2
        with pytest.raises(ProcessLookupError):
            os.kill(int(pids[0]), 0)
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, (stdout, stderr)
        with pytest.raises(ProcessLookupError):
            os.kill(int(pids[1]), 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
