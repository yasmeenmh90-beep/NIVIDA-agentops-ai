# Generic image for SANDBOX_MODE=docker. Tests run with --network none, so a project's
# dependencies must be baked in. Build one image per project and set it as the project's sandbox_image:
#   docker build -f sandbox/Dockerfile --build-arg REQUIREMENTS=path/to/requirements.txt -t agentops-sbx-myproj .
FROM python:3.11-slim
ARG REQUIREMENTS=sandbox/empty-requirements.txt
COPY ${REQUIREMENTS} /tmp/requirements.txt
RUN pip install --no-cache-dir pytest -r /tmp/requirements.txt && useradd -m runner
USER runner
WORKDIR /work
