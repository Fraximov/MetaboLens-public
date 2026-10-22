FROM python:3.12-slim

ARG DOCKER_CLI_VERSION=27.5.1
ARG NEXTFLOW_VERSION=25.10.4

WORKDIR /app
COPY worker/requirements.txt /app/worker-requirements.txt
COPY backend/requirements.txt /app/backend-requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends curl openjdk-21-jre procps ca-certificates tar openssh-client && \
    arch="$(dpkg --print-architecture)" && \
    case "${arch}" in \
      amd64) docker_arch="x86_64" ;; \
      arm64) docker_arch="aarch64" ;; \
      *) echo "Unsupported architecture: ${arch}" >&2; exit 1 ;; \
    esac && \
    curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz && \
    tar -xzf /tmp/docker.tgz -C /tmp && \
    mv /tmp/docker/docker /usr/local/bin/docker && \
    chmod +x /usr/local/bin/docker && \
    rm -rf /tmp/docker /tmp/docker.tgz && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://get.nextflow.io | NXF_VER="${NEXTFLOW_VERSION}" bash && \
    mv nextflow /usr/local/bin/nextflow && \
    chmod +x /usr/local/bin/nextflow && \
    pip install --no-cache-dir -r /app/worker-requirements.txt -r /app/backend-requirements.txt
COPY worker/tools/metabolights-requirements.txt /opt/metabolights-requirements.txt
RUN python -m venv /opt/metabolights && \
    /opt/metabolights/bin/python -m pip install --no-cache-dir --upgrade pip==26.2.2 && \
    /opt/metabolights/bin/pip install --no-cache-dir -r /opt/metabolights-requirements.txt && \
    arch="$(dpkg --print-architecture)" && \
    case "${arch}" in \
      amd64) opa_sha="2c0ccdbbe0b8e2a5d12d9c42d92f1f34f494ffb32d1f3c4ddc36101be637d66f" ;; \
      arm64) opa_sha="facd6a9ea375c6299701f86b90b470e52305c5726c4f136e2980fa6123ae9613" ;; \
      *) exit 1 ;; \
    esac && \
    curl -fsSL "https://github.com/open-policy-agent/opa/releases/download/v1.4.2/opa_linux_${arch}_static" -o /opt/metabolights/bin/opa && \
    echo "${opa_sha}  /opt/metabolights/bin/opa" | sha256sum -c - && \
    chmod +x /opt/metabolights/bin/opa
COPY worker/ /app/worker/
COPY backend/app /app/backend/app
ENV PYTHONPATH=/app/backend:/app/worker
CMD ["python", "/app/worker/main.py"]
