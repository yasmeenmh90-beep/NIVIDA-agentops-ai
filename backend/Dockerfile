# Build from the repo root:  docker build -f backend/Dockerfile -t agentops-backend .
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/backend
COPY backend/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
# Clones and per-project virtualenvs live here; mount a volume to keep them across restarts.
ENV WORKSPACE_DIR=/data/workspace SANDBOX_MODE=subprocess DATABASE_URL=sqlite:////data/agentops.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
