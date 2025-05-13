# Dockerfile
FROM python:3.9-slim

COPY consumer_model.py /app/consumer_model.py
WORKDIR /app
RUN pip install numpy pandas scikit-learn prometheus_client


CMD ["python", "consumer_model.py"]