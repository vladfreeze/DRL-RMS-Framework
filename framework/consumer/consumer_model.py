import time
from datetime import datetime

import numpy as np
import pandas as pd
from prometheus_client import Gauge, start_http_server
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier

# Define metric to track latency and be scraped by Prometheus
# This metric will be used to track the time taken to train the model
# and will be exposed on the /metrics endpoint

LATENCY = Gauge("model_training_latency_seconds", "Time spent training the model")


def generate_large_dataset():
    # Generate a large dataset to use a lot of RAM
    X, y = make_classification(
        n_samples=10000, n_features=100, n_informative=10, n_classes=2, random_state=42
    )
    return pd.DataFrame(X), pd.Series(y)


def train_model(X, y):
    # Train a complex model
    model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
    model.fit(X, y)
    return model


@LATENCY.time()
def train_and_measure_latency():
    start_time = datetime.now()

    # Generate large dataset
    X, y = generate_large_dataset()

    # Train the model
    model = train_model(X, y)

    end_time = datetime.now()
    latency = (end_time - start_time).total_seconds()
    LATENCY.set(latency)
    print(f"Training completed at {end_time} with latency: {latency} seconds")

    return latency


def main():
    start_http_server(8000)

    while True:
        train_and_measure_latency()

        # Sleep for 10 seconds
        time.sleep(10)


if __name__ == "__main__":
    main()
