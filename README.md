# Subprocess-based code runner (Python + JS, no Docker/privileged mode needed)

This replaces the Piston approach. Instead of nested Docker containers, it
runs submitted code as a plain subprocess with OS-level resource limits
(CPU time, memory, process count, open files) and a wall-clock timeout.
No `--privileged` flag, no Docker-in-Docker — so it deploys on Render's
free tier, or any plain PaaS, without hitting the wall we ran into with
Piston.

## Files

- `app.py` — the FastAPI service (`/execute`, `/health`)
- `requirements.txt` — Python deps
- `Dockerfile` — bundles Python 3.12 + Node 20 in one image

## 1. Deploy on Render

1. Push this folder to a GitHub repo.
2. Render dashboard → **New** → **Web Service** → connect the repo.
3. Environment: **Docker** (Render will detect the `Dockerfile` automatically).
4. No privileged toggle needed — leave defaults.
5. Deploy. Render gives you a public URL like `https://your-service.onrender.com`.

Confirm it's up:

```bash
curl https://your-service.onrender.com/health
# {"status": "ok"}
```

## 2. Calling it from your app

```bash
curl -X POST https://your-service.onrender.com/execute \
  -H "Content-Type: application/json" \
  -d '{
    "language": "python",
    "code": "print(int(input()) * 2)",
    "stdin": "21"
  }'
```

Response:

```json
{
  "stdout": "42\n",
  "stderr": "",
  "exit_code": 0,
  "timed_out": false,
  "error": null
}
```

Same shape for `"language": "javascript"`. Each test case for a candidate's
submission = one request, with their code as `code` and the test's input as
`stdin`. Diff `stdout` against expected output yourself — don't outsource
correctness judging to an LLM.

## 3. Secrets and config via .env

Two files changed to support this, plus two new files:

**`requirements.txt`** — added one line:
```
python-dotenv==1.0.1
```

**`app.py`** — three changes:
1. Added `from dotenv import load_dotenv` + `load_dotenv()` near the top,
   right after the other imports.
2. All the tunables (`CPU_TIME_LIMIT_SECONDS`, `WALL_TIME_LIMIT_SECONDS`,
   `MEMORY_LIMIT_MB`, `MAX_PROCESSES`, `MAX_OPEN_FILES`, `MAX_OUTPUT_BYTES`)
   now read from `os.environ.get(...)` with the old hardcoded numbers kept
   only as defaults.
3. Added `API_KEY = os.environ.get("API_KEY")` and a `require_api_key`
   dependency, wired into `/execute` via
   `dependencies=[Depends(require_api_key)]`. It fails closed — if
   `API_KEY` isn't set on the server, every call to `/execute` is refused
   rather than silently running open.

**New: `.env.example`** — template listing every variable above with
placeholder values. Commit this file.

**New: `.gitignore`** — contains `.env` so your real secret never gets
pushed to GitHub.

### Local development

```bash
cp .env.example .env
# edit .env, set a real API_KEY:
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### On Render

Render doesn't read your `.env` file at all — `.env` is a local-only
convention. Instead, set the same variables in the dashboard:

**Service → Environment → Add Environment Variable** — add `API_KEY` (and
any tunables you want to override) there. Render injects them as real
environment variables at runtime, which `os.environ.get(...)` picks up
exactly the same way.

### Calling the API now requires the header

```bash
curl -X POST https://your-service.onrender.com/execute \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-real-key-here" \
  -d '{"language": "python", "code": "print(1+1)"}'
```



## 4. Why the limits are set the way they are

- `CPU_TIME_LIMIT_SECONDS = 3` (RLIMIT_CPU) — kills genuinely CPU-bound
  infinite loops fast, regardless of wall-clock timing.
- `WALL_TIME_LIMIT_SECONDS = 5` — backstop for code that's mostly waiting
  (e.g. blocked I/O) rather than burning CPU.
- `MEMORY_LIMIT_BYTES = 200MB` (RLIMIT_AS) — **applied to Python only**.
  Node is deliberately excluded from this — V8 reserves a large virtual
  address space on startup regardless of actual usage, so a hard RLIMIT_AS
  kills Node before it can even run. Node's memory is capped instead via
  `--max-old-space-size=150` on the command line itself. Verified both
  paths behave correctly during testing.
- `MAX_PROCESSES = 20` (RLIMIT_NPROC) — blunts fork-bomb-style attacks.
- Non-root container user in the Dockerfile — one privilege restriction
  that actually helps here and needs no special platform support.

## 5. Known limitations (read this before going live)

This is meaningfully weaker isolation than Piston/Docker-based sandboxing.
Be honest with yourself about what it's for:

- **No network isolation.** A subprocess on a shared host can still make
  outbound requests unless the hosting platform itself blocks it at the
  network layer. If Render doesn't restrict egress from your service by
  default, candidate code could technically reach the internet. Check
  Render's current networking docs, and consider adding an explicit
  outbound-deny rule if available on your plan.
- **Shared filesystem/kernel with your own service.** Everything runs in
  the same container as your API, just as a different OS user. A
  sufficiently creative escape (kernel exploit, shared resource
  exhaustion) isn't blocked the way a real container boundary would block
  it. This is fine against typical interview-candidate code; it is not
  fine against a motivated attacker.
- **RLIMIT_NPROC is per-user, not per-job.** If multiple executions run
  concurrently as the same OS user, the process-count limit is shared
  across them, not isolated per request. Under real concurrent load this
  needs tightening (e.g. a small pool of dedicated OS users, one per
  concurrent slot).
- **This is appropriate for a proctored/known-candidate interview
  context**, not for an anonymous public code-execution API. If you ever
  open this up to arbitrary untrusted internet traffic, move back to
  real container/VM isolation (Piston on a proper VPS, or a managed
  sandboxing service).

## 6. Scaling notes

FastAPI here runs requests synchronously per worker — each `/execute` call
blocks a worker for up to `WALL_TIME_LIMIT_SECONDS`. For real concurrent
interview traffic, run multiple uvicorn workers (`--workers N`) sized to
your instance's CPU count, and consider a queue if you expect bursts.
