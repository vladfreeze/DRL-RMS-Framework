FROM python:3.9-slim

WORKDIR /app


RUN mkdir -p /app/drl-models
COPY drl-model.py /app/
COPY drl-models/model-meta-v9-2.h5 /app/drl-models
COPY drl-models/scaler-meta-v9-2.pkl /app/drl-models
COPY drl-models/deployment_embeddings.json /app/drl-models

RUN pip install --upgrade pip

ENV PIP_DEFAULT_TIMEOUT=100
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn tensorflow-cpu numpy joblib scikit-learn



EXPOSE 8000

CMD ["python", "drl-model.py"]