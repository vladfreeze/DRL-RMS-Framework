# Dockerfile
FROM python:3.9-slim-buster

# Kubectl

RUN apt-get update && \
    apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

COPY watcher/watcher.py /app/watcher.py
COPY models.py /app/models.py
COPY watcher/helpers.py /app/helpers.py
COPY watcher/metrics.py /app/metrics.py
COPY rms_config.py /app/rms_config.py
WORKDIR /app
RUN pip install kubernetes pydantic colorlog fastapi pandas prometheus_api_client


CMD ["python", "watcher.py"]