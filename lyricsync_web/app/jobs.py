import os
import sys
import threading
import subprocess
from pathlib import Path
from typing import Dict, Tuple, Any

class JobManager:
    def __init__(self, base_logs: Path):
        self.base_logs = Path(base_logs)
        self._lock = threading.Lock()
        self._jobs: Dict[Tuple[str, str], Tuple[subprocess.Popen, Any]] = {}

    def start(self, slug: str, job_name: str, cmd, cwd: Path):
        cwd = Path(cwd)
        logs_dir = cwd / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_path = logs_dir / f"{job_name}.log"

        # Ensure python interpreter at front if launching a .py script
        python = sys.executable or "python"
        full_cmd = [python] + cmd if cmd and str(cmd[0]).endswith(".py") else cmd

        logf = open(log_path, "wb")
        proc = subprocess.Popen(full_cmd, cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT)

        key = (slug, job_name)
        with self._lock:
            self._jobs[key] = (proc, logf)
        t = threading.Thread(target=self._wait_and_clean, args=(key,), daemon=True)
        t.start()
        return f"{slug}:{job_name}"

    def _wait_and_clean(self, key):
        job_data = self._jobs.get(key)
        if not job_data: return
        proc, logf = job_data
        proc.wait()
        if logf:
            logf.close()

    def status(self, slug: str, job_name: str):
        key = (slug, job_name)
        job_data = self._jobs.get(key)
        if not job_data:
            return {"running": False}
        proc, _ = job_data
        code = proc.poll()
        return {"running": code is None, "returncode": code}