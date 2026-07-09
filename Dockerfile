# Pinned to Debian Bullseye specifically (not just "slim", which now floats
# to Bookworm/glibc 2.36+). Bullseye ships glibc 2.31 — below the 2.34
# threshold where glibc's pthread_create started defaulting to the clone3
# syscall. Render's free-tier sandbox appears to block clone3 outright
# (regardless of libc — we confirmed this isn't glibc-specific by testing
# musl/Alpine, which hit the identical crash), so the fix is to avoid ever
# calling it in the first place by staying on an older libc.
FROM python:3.12-slim-bullseye

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

RUN useradd --create-home runner
USER runner

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]