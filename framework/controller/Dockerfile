# Dockerfile
FROM python:3.9-slim-buster

COPY controller/controller.py /app/controller/controller.py
COPY controller/frontend /app/controller/frontend
COPY controller/utils.py /app/utils.py
COPY controller/__init__.py /app/controller/__init__.py
COPY __init__.py /app/__init__.py 
COPY models.py /app/models.py
COPY metrics.py /app/metrics.py
COPY rms_config.py /app/rms_config.py
WORKDIR /app

RUN apt-get update && \
    apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

RUN pip install fastapi uvicorn kubernetes openai python-multipart prometheus_api_client colorlog


CMD ["python", "-m", "controller.controller"]