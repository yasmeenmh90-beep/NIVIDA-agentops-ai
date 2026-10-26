# Build from the repo root:  docker build -f backend/Dockerfile -t agentops-backend .
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
COPY demo-app/requirements.txt demo-app/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt -r demo-app/requirements.txt
COPY backend backend
COPY demo-app demo-app
WORKDIR /srv/backend
ENV TARGET_REPO_PATH=/srv/demo-app SANDBOX_MODE=subprocess
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
