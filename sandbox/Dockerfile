# Image used for SANDBOX_MODE=docker. Tests run with --network none, so deps are baked in here.
FROM python:3.11-slim
COPY demo-app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd -m runner
USER runner
WORKDIR /work
