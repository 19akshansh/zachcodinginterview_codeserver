# Bullseye pin kept for safety/consistency, though the clone3 restriction
# this originally worked around was specific to Render's free-tier sandbox,
# not Docker/glibc in general. On a real VM (e.g. a self-hosted VPS) this
# would likely work fine even on -bookworm; no reason to change it back.
FROM python:3.12-slim-bullseye

# Install Node.js so the runner can execute JS submissions too. Fine on a
# real VM/VPS with normal Docker — just not on Render's free tier, where
# this specific base still hits the clone3/seccomp crash from before.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Run as a non-root user — this is the one privilege restriction that
# actually helps here, and it works fine without --privileged.
RUN useradd --create-home runner
USER runner

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]