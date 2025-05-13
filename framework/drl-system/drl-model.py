print("started")
import json
from collections import defaultdict, deque

import joblib
import numpy as np
import tensorflow as tf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# Enable eager execution
tf.compat.v1.enable_eager_execution()
assert tf.executing_eagerly(), "Eager execution is not enabled"

app = FastAPI()

model = tf.keras.models.load_model("drl-models/model-meta-v9-2.h5")
scaler = joblib.load("drl-models/scaler-meta-v9-2.pkl")

# Memory buffer per deployment
deployment_memory = defaultdict(deque)  # Store last N experiences per deployment
reward_memory = defaultdict(deque)  # Store rewards
embedding_dim = 4  # Set embedding size
max_memory = 50  # Max experiences per deployment
deployment_models = {}


class NewData(BaseModel):
    deployment_id: str
    replicas: float
    initial_cpu_usage: float
    applied_cpu_usage: float
    initial_memory_usage: float
    applied_memory_usage: float
    initial_avg_latency: float
    latest_avg_latency: float


class FeedbackData(BaseModel):
    id: str
    correct_label: int
    reward: float


def load_embeddings(file_path="drl-models/deployment_embeddings.json"):
    with open(file_path, "r") as f:
        return json.load(f)


deployment_embeddings = load_embeddings()
prediction_tracking = {}


@app.post("/predict/")
async def predict(data: NewData):
    global deployment_embeddings, deployment_models
    try:
        # Normalize numerical features
        new_data = np.array(
            [
                [
                    data.replicas,
                    data.initial_cpu_usage,
                    data.applied_cpu_usage,
                    data.initial_memory_usage,
                    data.applied_memory_usage,
                    data.initial_avg_latency,
                    data.latest_avg_latency,
                ]
            ]
        )
        new_data_scaled = scaler.transform(new_data)
        deployment_id = data.deployment_id
        # Assign embedding index or create a new one
        if deployment_id not in deployment_embeddings:
            deployment_embeddings[deployment_id] = len(deployment_embeddings)

        deployment_idx = np.array([deployment_embeddings[deployment_id]])

        # Retrieve past experiences (use last N, not mean)
        past_memory = list(deployment_memory[deployment_id])[-10:]
        if past_memory:
            past_memory = np.mean(past_memory, axis=0, keepdims=True)
        else:
            past_memory = np.zeros_like(new_data_scaled)

        # Check if a specific model exists for the deployment
        model_path = f"drl-models/rl_k8s_model_{deployment_id}.keras"
        print("checking model name", deployment_models)
        print("check for name", deployment_id)
        if deployment_id in deployment_models.keys():
            print("is in")
            print(deployment_models[deployment_id])
            specific_model = deployment_models[deployment_id]
            print(specific_model.name)
        elif tf.io.gfile.exists(model_path):
            specific_model = tf.keras.models.load_model(model_path)
            deployment_models[deployment_id] = specific_model
            print(specific_model.name)
        else:
            print("also else")
            specific_model = model  # Use the global model
        print("using specific model", specific_model.name)

        # Make prediction
        predicted_probs = specific_model.predict([deployment_idx, new_data_scaled])
        predicted_class = np.argmax(predicted_probs, axis=1)[0]

        # Store experience
        deployment_memory[deployment_id].append(new_data_scaled.flatten())

        return {"deployment_id": deployment_id, "predicted_class": int(predicted_class)}

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/feedback/")
async def feedback(feedback: FeedbackData):
    global deployment_models
    try:
        deployment_id = feedback.id
        # Store reward only for this deployment
        reward_memory[deployment_id].append(feedback.reward)

        # Retrieve stored experiences only for this deployment
        batch_X, batch_y = [], []
        deployment_idx = np.array(
            [[deployment_embeddings[deployment_id]]]
        )  

        for memory in deployment_memory[
            deployment_id
        ]:  # Use only this deployment's experiences
            batch_X.append(memory)
            batch_y.append(
                feedback.correct_label
            )  # Append the correct label for each experience

        if not batch_X:  # Ensure training data exists
            return {"message": "No stored experiences for training."}

        # Ensure batch_X and batch_y have the same number of samples
        batch_X = [np.array([deployment_idx[0]] * len(batch_X)), np.vstack(batch_X)]
        batch_y = np.array(batch_y)

        # Adjust embeddings for only this deployment
        if reward_memory[deployment_id]:
            avg_reward = np.mean(reward_memory[deployment_id])
            if deployment_id in deployment_embeddings:
                deployment_embeddings[deployment_id] += (
                    0.01 * avg_reward
                )  
        # Create or load a deployment-specific model
        if deployment_id in deployment_models:
            specific_model = deployment_models[deployment_id]
            print("dep already in models")
        else:
            specific_model = tf.keras.models.clone_model(model)
            specific_model.set_weights(model.get_weights())
            specific_model._name = f"model_{deployment_id}"
            print("added dep to models")

        # Train the deployment-specific model
        specific_model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=0.001),
            loss="sparse_categorical_crossentropy",
            metrics=["accuracy"],
        )
        specific_model.fit(batch_X, batch_y, epochs=3, verbose=0)

        # Clone the specific model with a new name
        deployment_models[deployment_id] = specific_model
        specific_model.name = "TEST-MODEL" + str(deployment_id)
        specific_model.save(f"drl-models/rl_k8s_model_{deployment_id}.keras")
        return {
            "message": f"Feedback applied for deployment {deployment_id}, saved model."
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
