"""
Lightweight code-execution runner for an interview platform.

Runs untrusted Python/JS as a subprocess with OS-level resource limits
instead of relying on Docker/privileged containers. Weaker isolation than
Piston, but deployable anywhere that needs no special container
privileges — including directly inside Termux on a phone.

History note: Node.js was previously dropped when this ran on Render's free
tier, because Render's sandbox blocks the clone3 syscall Node's thread
startup depends on (Assertion failed: (0) == (uv_thread_create(...))).
That's a host-level restriction of Render's shared containers specifically
— it does not apply when running directly on a real Linux kernel (a normal
VPS, or Termux on Android), so JavaScript is back in for those targets.
If you deploy this back onto Render's free tier, expect the JS runtime to
fail there again for the same reason as before.

NOT bulletproof — see README "Known limitations" before relying on this
for anonymous, unproctored, adversarial users. This matters even more if
you're running it on your own phone via Termux: the sandboxing below is
what stands between candidate-submitted code and your actual device.
"""

import os
import resource
import shutil
import subprocess
import tempfile
import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

load_dotenv()  # reads .env in local dev / Termux; on Render, env vars come from the dashboard instead

app = FastAPI()

# ---- Tunables (all overridable via env vars, sane defaults if unset) ------

CPU_TIME_LIMIT_SECONDS = int(os.environ.get("CPU_TIME_LIMIT_SECONDS", 3))
WALL_TIME_LIMIT_SECONDS = int(os.environ.get("WALL_TIME_LIMIT_SECONDS", 5))
MEMORY_LIMIT_BYTES = int(os.environ.get("MEMORY_LIMIT_MB", 200)) * 1024 * 1024
MAX_PROCESSES = int(os.environ.get("MAX_PROCESSES", 64))
MAX_OPEN_FILES = int(os.environ.get("MAX_OPEN_FILES", 64))
MAX_OUTPUT_BYTES = int(os.environ.get("MAX_OUTPUT_BYTES", 100_000))

# ---- Secret: required to call /execute -------------------------------------
API_KEY = os.environ.get("API_KEY")


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    if not API_KEY:
        # Fail closed: if no key is configured, refuse rather than run open.
        raise HTTPException(status_code=500, detail="Server misconfigured: API_KEY not set")
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")


def _apply_limits_python():
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME_LIMIT_SECONDS, CPU_TIME_LIMIT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _apply_limits_node():
    # IMPORTANT: do NOT set RLIMIT_AS for Node. V8 reserves a large chunk of
    # virtual address space on startup regardless of actual heap usage, so a
    # hard RLIMIT_AS kills the process before it even runs. Memory is instead
    # bounded via the --max-old-space-size flag on the node command itself.
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME_LIMIT_SECONDS, CPU_TIME_LIMIT_SECONDS))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (MAX_OPEN_FILES, MAX_OPEN_FILES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


RUNTIMES = {
    "python": {"cmd": ["python3", "{file}"], "ext": "py", "limits": _apply_limits_python},
    "javascript": {
        "cmd": ["node", "--v8-pool-size=0", "--max-old-space-size=150", "{file}"],
        "ext": "js",
        "limits": _apply_limits_node,
    },
}


class ExecuteRequest(BaseModel):
    language: str
    code: str
    stdin: Optional[str] = ""


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool
    error: Optional[str] = None


def _truncate(text: str, limit: int = MAX_OUTPUT_BYTES) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore") + "\n...[truncated]"


@app.post("/execute", response_model=ExecuteResponse, dependencies=[Depends(require_api_key)])
def execute(req: ExecuteRequest):
    if req.language not in RUNTIMES:
        supported = ", ".join(sorted(RUNTIMES))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: {req.language}. Supported: {supported}",
        )

    runtime = RUNTIMES[req.language]
    job_id = str(uuid.uuid4())
    job_dir = os.path.join(tempfile.gettempdir(), f"job-{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    file_path = os.path.join(job_dir, f"main.{runtime['ext']}")
    with open(file_path, "w") as f:
        f.write(req.code)

    cmd = [part.format(file=file_path) for part in runtime["cmd"]]

    # Minimal, clean environment — don't leak host secrets/env vars into
    # candidate code. UV_THREADPOOL_SIZE keeps Node's libuv thread pool
    # small on purpose — cheap to keep even where clone3 isn't an issue.
    clean_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": job_dir,
        "UV_THREADPOOL_SIZE": "1",
    }

    timed_out = False
    stdout, stderr, exit_code = "", "", None

    try:
        proc = subprocess.run(
            cmd,
            input=req.stdin or "",
            capture_output=True,
            text=True,
            cwd=job_dir,
            env=clean_env,
            timeout=WALL_TIME_LIMIT_SECONDS,
            preexec_fn=runtime["limits"],   # Linux only, language-specific
        )
        stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout or ""
        stderr = (e.stderr or "") + "\n[Killed: exceeded time limit]"
    except Exception as e:
        stderr = f"Internal runner error: {e}"
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    return ExecuteResponse(
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        exit_code=exit_code,
        timed_out=timed_out,
    )


@app.get("/health")
def health():
    return {"status": "ok", "build": "python-js-1", "languages": sorted(RUNTIMES)}