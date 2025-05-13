# Use TensorFlow GPU Jupyter image as the base image
FROM tensorflow/tensorflow:latest-gpu-jupyter

# Install additional dependencies if needed
RUN pip install  tensorflow[and-cuda] tf_keras pandas scikit-learn prometheus_api_client kubernetes pydantic colorlog 

# Set environment variable to use legacy Keras
ENV TF_USE_LEGACY_KERAS=True


# Set the command to run the Jupyter notebook server
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--allow-root", "--no-browser", "--LabApp.token='passwd123'"]