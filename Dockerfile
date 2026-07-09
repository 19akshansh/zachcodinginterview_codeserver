FROM python:3.12-alpine

# Alpine uses musl libc instead of glibc. This matters specifically for
# Node.js: glibc >= 2.34 uses the clone3 syscall for pthread_create by
# default, and some restricted/sandboxed container runtimes (seen on
# Render's free tier) block clone3 via seccomp — which crashes Node.js on
# startup with "Assertion failed: (0) == (uv_thread_create(...))" before
# any script even runs. Musl's pthread implementation doesn't have this
# dependency, so Node runs fine here. (See nodejs/node#43064 for the
# upstream bug writeup.)
RUN apk add --no-cache nodejs npm

WORKDIR /app
COPY requirements.txt .

# fastapi/pydantic/python-dotenv are pure Python or ship musllinux wheels,
# so this installs cleanly without extra build tooling in the common case.
# If pip ever fails trying to compile something from source here, add:
#   RUN apk add --no-cache gcc musl-dev python3-dev
# above this line, then retry.
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as a non-root user — this is the one privilege restriction that
# actually helps here, and it works fine without --privileged.
RUN adduser -D runner
USER runner

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]