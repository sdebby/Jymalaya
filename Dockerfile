# Jymalaya-C1 -- backend + built dashboard in one image.
#
# Stage 1 builds the Vite SPA; stage 2 runs uvicorn and serves that build from
# frontend_dist/ at /, with the REST API under /api (see API_PREFIX in main.py).
# Build:  docker compose -f docker-compose.deploy.yml build

# ---------------------------------------------------------------- frontend ---
FROM node:22-alpine AS ui

WORKDIR /ui

# Dependencies first so an unchanged lockfile keeps the npm layer cached.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# "npm run build" is "tsc -b && vite build" -- the type check is part of it.
RUN npm run build

# ----------------------------------------------------------------- runtime ---
FROM python:3.12-slim

# tzdata: the container runs on the athlete's TZ (compose sets TZ), which the
# plan-freshness checks depend on. curl: the healthcheck below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && rm -rf /var/lib/apt/lists/*

# Non-root, with a real home: the Garmin token cache and the MFA handoff file
# live at ~/.tricoach (app/garmin/client.py), which compose mounts as a volume.
RUN useradd --create-home --home-dir /home/app --uid 1000 app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY app/ ./app/
COPY config/ ./config/
COPY prompts/ ./prompts/
COPY scripts/ ./scripts/
COPY --from=ui /ui/dist ./frontend_dist/

# Writable state, all three mounted as named volumes in compose: data/, config/
# (athlete.local.yaml) and ~/.tricoach (Garmin tokens). Every one of them must
# exist here first -- Docker copies the image directory's ownership onto a new
# named volume, and a volume with no directory to copy from lands root-owned
# and unwritable.
RUN mkdir -p /app/data /home/app/.tricoach \
 && chown -R app:app /app/data /app/config /home/app

USER app
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8080/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
