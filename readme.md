# DRL-RMS

A Deep Reinforcement Learning framework for simulating and resolving resource management conflicts in cloud environments.

# Installation on an available Kubernetes cluster

## Cluster services
* Set up private image registry
* Set up monitoring stack with custom configuration included in `cluster-services/monitoring`(Prometheus, Grafana)
* For networking, set up MetalLB for external IP address allocation or set up nginx ingress controller.
* The GPU-Juypter dockerfile is included in the `cluster-services` folder, which can be used to train models with GPU support. It can either be deployed on the cluster or run locally with Docker Desktop.

## Framework Setup and Configuration
* Before deploying each component, configure the desired parameters in the `rms_config.py` file. Target namespaces are namespaces which contain the deployments that may be affected by the custom resource quotas.
This framework was tested on the target namespace `sim-env` and in the management namespace `resource-management`.
* Endpoints also need to be configured before deploying. They can be configured in the `rms_config.py` file. For the yaml specifications, they have to be entered manually.
* The controller, watcher and DRL-System should be deployed in the management namespace `resource-management`, while the consumer services should be deployed in the target namespace `sim-env`.
* The data viewed in the dashboard overview is based on the data collected by the watcher. In the monitoring tab, the three dashboard share links have to be configured in the Grafana instance and afterwards pasted in `framework/controller/frontend/monitoring.html`. More details regarding the dashboard configuration can be found in the Grafana section below.



### 1. Agent 
* To create the agent CRD, simply run the following command from `framework/agent`:
~~~
kubectl apply -f agentCRD.yaml
~~~

### 2. Controller 
* Before building and deploying the controller, first set up 
* To enable LLM assitance, create an OpenAI API key and save it as a secret in the cluster, using the command:

~~~
kubectl create secret generic openai-api-secret --from-literal=api-key=your_openai_api_key
~~~

* Before deploying the controller, make sure to apply the `controller-permissions.yaml` and `controller-service.yaml` files to the cluster. These files contain the necessary permissions and service account for the controller to function properly.

* The controller image can be built with this command from the framework directory:
~~~
docker build -f controller/Dockerfile -t registry-endpoint/controller:tw-1 . ; docker push registry-endpoint/controller:tw-1; k delete -f controller/controller-depl.yaml ; k apply -f controller/controller-depl.yaml 
~~~


### 3. Watcher 
* Before deploying the watcher, make sure to apply the `watcher-permissions.yaml` file to the cluster. 
* The watcher image can be built with this command from the framework directory:

~~~
docker build -f watcher/Dockerfile -t registry-endpoint/watcher:v9. ; docker push registry-endpoint/watcher:v9; k delete -f watcher/watcher-depl.yaml -n resource-management ; k apply -f watcher/watcher-depl.yaml -n resource-management
~~~

### 4. DRL-System 

* An existing DRL model is provided to test the framework. However, the model can be re-trained using the available notebooks.
* The DRL-System image can be built using the Dockerfile included in the `drl-system` folder. 


## How to use

1. First deploy some target services that will be managed by the framework. Wait a couple of minutes until Prometheus has scraped the data, so that it can be fetched by the watcher.
2. In the controller, create an agent either by using the form or with the llm assistant. The agent will then appear in the dashboard. 
3. As soon as the agent is created, the watcher will apply its resource quota on the services that are targeted by the scope. 
4. For resource quotas which limit the usage considerably, performance degradation may be observed. This would then be tracked by the watcher and then corrected by the DRL system.
5. Conflicts can also be triggered by creating new agents with different resource quotas where the targets overlap. After three iterations, the conflicts will be detected by the framework.


## Training the DRL model
The model can be trained using the provided notebooks. The training process is as follows:
1. Generate the training data by running `df-gen.ipynb`. This will create four csv files containing different cases for each decision.
2. Build and train the model using `dl-model-training.ipynb`. This will output the `model.h5`, `scaler.pkl` and `deployment_embeddings.json` files, which are used by the DRL system.

## Sending Latency to prometheus

Prometheus has to be configured to scrape additional metrics from the target services.
* Include prometheus package in python scripts and have them host :8000/metrics, which makes the data available for Prometheus. 
* If Prometheus is deployed as a helm chart, the additional scrape jobs can be added in `cluster-services/monitoring/prometheus_config.yaml` under the `additionalScrapeConfigs` section.



### Grafana Dashboard Configuration

The monitoring dashboards used by the controller have to be first configured in the Grafana instance. These are the relevant queries used in the framework:


#### Latency
~~~
avg by(deployment) (
  model_training_latency_seconds{namespace="sim-env"}
)
~~~

#### Memory

~~~
sum(
    container_memory_working_set_bytes{job="kubelet", metrics_path="/metrics/cadvisor", cluster="$cluster", namespace="$namespace"}
  * on(namespace,pod)
    group_left(workload, workload_type) namespace_workload_pod:kube_pod_owner:relabel{cluster="$cluster", namespace="$namespace", workload_type=~"$type"}
) by (workload, workload_type)

~~~

#### CPU
~~~
sum(
  node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{cluster="$cluster", namespace="$namespace"}
* on(namespace,pod)
  group_left(workload, workload_type) namespace_workload_pod:kube_pod_owner:relabel{cluster="$cluster", namespace="$namespace", workload_type=~"$type"}
) by (workload, workload_type)

~~~