"""Run the browser smoke tests end to end, on their own copy of everything.

    python scripts/run_e2e.py

Starts a separate sandbox (port 8101), backend (8001) and frontend (5174) with their own data in
var/e2e, seeds the demo shop, runs the Playwright tests in frontend/e2e with the installed Microsoft
Edge, then stops all three. The developer's own servers (8100/8000/5173) and var/ are never touched,
and the model is the offline mock, so a run costs nothing. The demo PIN is read from .env and handed
to the tests through the environment; it is never printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT)]

from scripts.seed import ScriptSecrets  # noqa: E402

DATA = ROOT / "var" / "e2e"
PORTS = {"sandbox": 8101, "backend": 8001, "frontend": 5174}
PY = sys.executable


def wait_for(url: str, name: str, seconds: int = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception:  # noqa: BLE001  (not up yet)
            time.sleep(0.5)
    raise SystemExit(f"{name} did not start at {url}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":  # npm and uvicorn start children: stop the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        process.terminate()


def main() -> int:
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True)
    env = {**os.environ, "DATA_DIR": str(DATA), "SANDBOX_DATA_DIR": str(DATA / "sandbox"),
           "SANDBOX_URL": f"http://127.0.0.1:{PORTS['sandbox']}",
           "FRONTEND_ORIGIN": f"http://localhost:{PORTS['frontend']}",
           "BACKEND_URL": f"http://127.0.0.1:{PORTS['backend']}", "LLM_MODE": "mock",
           "PYTHONIOENCODING": "utf-8"}
    logs = {name: open(DATA / f"{name}.log", "w", encoding="utf-8") for name in PORTS}  # noqa: SIM115
    running: list[subprocess.Popen] = []
    try:
        running.append(subprocess.Popen(
            [PY, "-m", "uvicorn", "sandbox.app:create_app", "--factory", "--port", str(PORTS["sandbox"])],
            cwd=ROOT, env=env, stdout=logs["sandbox"], stderr=subprocess.STDOUT))
        wait_for(f"http://127.0.0.1:{PORTS['sandbox']}/health", "the sandbox")
        seeded = subprocess.run([PY, "scripts/seed.py"], cwd=ROOT, env=env, capture_output=True, text=True,
                                encoding="utf-8")
        if seeded.returncode:
            print(seeded.stdout[-2000:], seeded.stderr[-2000:])
            return seeded.returncode
        running.append(subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:create_app", "--factory", "--app-dir", "backend",
             "--port", str(PORTS["backend"])], cwd=ROOT, env=env, stdout=logs["backend"], stderr=subprocess.STDOUT))
        wait_for(f"http://127.0.0.1:{PORTS['backend']}/api/health", "the backend")
        npm = "npm.cmd" if os.name == "nt" else "npm"
        running.append(subprocess.Popen(
            [npm, "run", "dev", "--", "--port", str(PORTS["frontend"])], cwd=ROOT / "frontend", env=env,
            stdout=logs["frontend"], stderr=subprocess.STDOUT))
        wait_for(f"http://localhost:{PORTS['frontend']}/", "the frontend")
        npx = "npx.cmd" if os.name == "nt" else "npx"
        tests = subprocess.run(
            [npx, "playwright", "test", *sys.argv[1:]], cwd=ROOT / "frontend",
            env={**env, "KP_E2E_URL": f"http://localhost:{PORTS['frontend']}",
                 "KP_E2E_PIN": ScriptSecrets().demo_owner_pin.get_secret_value()})
        return tests.returncode
    finally:
        for process in reversed(running):
            stop(process)
        for log in logs.values():
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
