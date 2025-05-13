import json
import logging
import math

import colorlog
import numpy as np
import pandas as pd
import requests
from fastapi import Request
from kubernetes import client

from metrics import (
    query_avg_latency_1h,
    query_avg_latency_latest,
    query_cpu_usage,
    query_cpu_workload_usage,
    query_memory_usage,
    query_memory_workload_usage,
)
from models import AgentDTO, ResourceQuota, Resources
from rms_config import rms_config

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
# Configure colored logging
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s - %(levelname)s - %(message)s",
        log_colors={
            "DEBUG": "cyan",
            "INFO": "green",
            "WARNING": "bold_yellow",
            "ERROR": "red",
            "CRITICAL": "bold_red",
        },
    )
)
logger = colorlog.getLogger()
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Remove any other handlers to avoid duplicate logs
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(handler)


# Get model endpoint from environment variable or use default
drl_system_endpoint = rms_config.get(
    "drl_system_endpoint",
    "http://drl-model.resource-management.svc.cluster.local:8000/predict/",
)
model_feedback_endpoint = drl_system_endpoint.replace("predict", "feedback")

# Load latency threshold configuration

config_latency_threshold = rms_config.get("drl_system", {}).get(
    "default_latency_threshold", 1.2
)
minimum = rms_config.get("drl_system", {}).get("minimum_reward", -10)


def resolve_agent_specification_conflict(agentDTOs, custom_api_instance):
    conflicting_agents = [agent for agent in agentDTOs if agent.state == "conflicting"]
    if len(conflicting_agents) > 1:  # Only check if there are more conflicting agents
        # Find the latest conflicting agent based on the smallest age
        latest_conflicting_agent = min(conflicting_agents, key=lambda agent: agent.age)
        latest_conflicting_agent.state = "disabled"
        print(latest_conflicting_agent)
        print(latest_conflicting_agent.name)
        print(latest_conflicting_agent.namespace)
        custom_api_instance.patch_namespaced_custom_object(
            group="resourcemanagement-v.io",
            version="v1",
            plural="agents",
            namespace=latest_conflicting_agent.namespace,
            name=latest_conflicting_agent.name,
            body={"spec": {"status": "disabled"}},
        )
        logger.info(
            "Set state of Agent %s to 'disabled' to resolve conflict. (Age: %s)",
            latest_conflicting_agent.name,
            latest_conflicting_agent.age,
        )

        # Set the state of other conflicting agents to "pending"
        for agent in conflicting_agents:
            if agent != latest_conflicting_agent:
                agent.state = "pending"
                print(agent)
                print(agent.name)
                print(agent.namespace)
                custom_api_instance.patch_namespaced_custom_object(
                    group="resourcemanagement-v.io",
                    version="v1",
                    plural="agents",
                    namespace=agent.namespace,
                    name=agent.name,
                    body={"spec": {"status": "pending"}},
                )
                logger.info(
                    "Set state of Agent %s to 'pending'. (Age: %s)",
                    agent.name,
                    agent.age,
                )


def apply_based_on_scope(
    agent: AgentDTO, watcher_state, api_instance=None, core_v1=None
):
    global df
    global iteration_conflict_dict
    global json_data
    custom_api_instance = client.CustomObjectsApi()
    if api_instance is None:
        api_instance = client.AppsV1Api()
    if core_v1 is None:
        core_v1 = client.CoreV1Api()
    patched = False
    df = watcher_state.df
    iteration_conflict_dict = watcher_state.iteration_conflict_dict
    json_data = watcher_state.json_data
    if agent.scope in ["namespace", "node", "deployment"]:
        df, df_target = check_deployments_for_agent_scope(df, agent, api_instance)
        for index, deployment in df_target.iterrows():

            deployment_obj = api_instance.read_namespaced_deployment(
                deployment["deployment_name"], deployment["namespace"]
            )

            if deployment_obj is None or deployment_obj.metadata is None:
                logger.warning(
                    "Skipping object %s in namespace %s due to missing deployment object or metadata",
                    deployment["deployment_name"],
                    deployment["namespace"],
                )
                continue
            # Check if the deployment has already been processed
            if (
                deployment_obj.metadata.annotations
                and deployment_obj.metadata.annotations.get("processed-by-agent")
                == agent.name
            ):
                logger.info(
                    "Deployment %s in namespace %s has already been processed by agent %s.",
                    deployment["deployment_name"],
                    deployment["namespace"],
                    agent.name,
                )

                if (
                    deployment_obj.metadata.annotations.get(
                        "resource-management/priority"
                    )
                    != "high"
                ):
                    logger.info(
                        "Checking the performance of deployment %s",
                        deployment["deployment_name"],
                    )
                    # handles oom killed, also reverts and sets to high priority.

                    fct = check_performance_of_optimized_deployment(
                        api_instance,
                        core_v1,
                        deployment,
                        f'app={deployment["deployment_name"]}',
                        agent,
                        model_feedback_endpoint,
                    )
                    if fct:
                        df.loc[index, "action"] = None
                        df.loc[index, "applied_memory_usage"] = None
                    continue
                else:
                    continue
                # Check if deployment actual usage is still in range of applied usage

            if (
                deployment_obj.metadata.annotations.get("resource-management/priority")
                == "high"
            ):
                logger.info(
                    "Deployment %s in namespace %s is of high priority and will be skipped.",
                    deployment["deployment_name"],
                    deployment["namespace"],
                )
                continue

            quota_usage = None
            cpu_quota_usage = None
            quota_usage_number = None
            cpu_quota_usage_number = None

            quota_usage, quota_usage_number, json_data, change_in_usage = (
                update_resource_usage(
                    resource_type="memory",
                    quota_factor=agent.resources.resourceQuota.memory,
                    initial_usage_key="initial_memory_usage",
                    applied_usage_key="applied_memory_usage",
                    quota_suffix="Mi",
                    deployment=deployment,
                    json_data=json_data,
                    df_target=df_target,
                    index=index,
                )
            )
            cpu_quota_usage, cpu_quota_usage_number, updated_json, change_in_usage = (
                update_resource_usage(
                    resource_type="cpu",
                    quota_factor=agent.resources.resourceQuota.cpu,
                    initial_usage_key="initial_cpu_usage",
                    applied_usage_key="applied_cpu_usage",
                    quota_suffix="m",
                    deployment=deployment,
                    json_data=json_data,
                    df_target=df_target,
                    index=index,
                    multiplier=1000,
                )
            )
            if change_in_usage:
                logger.info(
                    "Deployment %s does not respect the applied resource usage. ",
                    deployment["deployment_name"],
                )
                patched = True
            # Prepare input data for DRL Model
            input_data = updated_json[deployment["deployment_name"]].copy()
            if "action" in input_data:
                del input_data["action"]
            # Check if any value in input_data is None and skip this iteration if so
            if any(value is None for value in input_data.values()) or any(
                value == 0 for value in input_data.values()
            ):

                logger.warning(
                    "Skipping input_data due to None or 0 value: %s", input_data
                )
                continue

            # Check if input_data has the 'prediction' key and remove it
            if "prediction" in input_data:
                del input_data["prediction"]

            print(input_data)
            # Make prediction by calling the model container's endpoint
            try:
                response = requests.post(drl_system_endpoint, json=input_data)
                response.raise_for_status()
                prediction = response.json().get("predicted_class")
            except requests.exceptions.RequestException as e:
                logger.error("Error calling model endpoint: %s", e)
                continue

            print("RESPONSE IS ", prediction)

            # Update the DataFrame with the prediction value
            df.loc[index, "action"] = prediction
            if (
                isinstance(df.loc[index, "applied_agents"], list)
                and len(df.loc[index, "applied_agents"]) > 0
            ):
                df.loc[index, "applied_agents"].append(agent.name)
            else:
                df.at[index, "applied_agents"] = [agent.name]
            # Implement actions based on the prediction
            if prediction == 0:
                logger.info(
                    "Received suggestion to optimize deployment %s",
                    deployment["deployment_name"],
                )
                if quota_usage_number or cpu_quota_usage_number:
                    apply_agent_to_deployment(
                        deployment.deployment_name,
                        agent,
                        api_instance,
                        deployment,
                        (
                            quota_usage if quota_usage else None
                        ),  # Pass None if quota_usage is not available
                        (
                            cpu_quota_usage if cpu_quota_usage else None
                        ),  # Pass None if cpu_quota_usage is not available
                    )
                    if quota_usage_number:
                        df.loc[index, "applied_memory_usage"] = quota_usage_number
                    if cpu_quota_usage_number:
                        df.loc[index, "applied_cpu_usage"] = cpu_quota_usage_number
                else:
                    logger.warning(
                        "Skipping optimization for deployment %s due to missing quota_usage or cpu_quota_usage",
                        deployment["deployment_name"],
                    )
            elif prediction == 1:
                logger.info(
                    "Received suggestion to migrate deployment %s to another node",
                    deployment["deployment_name"],
                )
                evict_pod_from_node(agent, core_v1, api_instance, deployment)
            elif prediction == 2:
                logger.info(
                    "Received suggestion to scale up deployment %s",
                    deployment["deployment_name"],
                )
                scale_body = {"spec": {"replicas": deployment_obj.spec.replicas + 1}}
                api_instance.patch_namespaced_deployment_scale(
                    name=deployment["deployment_name"],
                    namespace=deployment["namespace"],
                    body=scale_body,
                )
            elif prediction == 3:
                logger.info(
                    "Received suggestion to scale down deployment %s",
                    deployment["deployment_name"],
                )
                if deployment_obj.spec.replicas > 1:
                    scale_body = {
                        "spec": {"replicas": deployment_obj.spec.replicas - 1}
                    }
                    api_instance.patch_namespaced_deployment_scale(
                        name=deployment["deployment_name"],
                        namespace=deployment["namespace"],
                        body=scale_body,
                    )

                    # TODO else bad feedback!
            if prediction in [0, 1, 2, 3]:
                body = {
                    "metadata": {
                        "annotations": {"processed-by-agent": agent.name},
                        "labels": {"managed-by-agent": agent.name},
                    }
                }
                api_instance.patch_namespaced_deployment(
                    name=deployment["deployment_name"],
                    namespace=deployment["namespace"],
                    body=body,
                )
                logger.info(
                    "Applied optimization step for %s in namespace %s: %s",
                    deployment["deployment_name"],
                    deployment["namespace"],
                    deployment["applied_memory_usage"],
                )
                logger.info(
                    "Applied optimization step for %s in namespace %s: %s",
                    deployment["deployment_name"],
                    deployment["namespace"],
                    deployment["applied_memory_usage"],
                )
                patched = True

    if patched and agent.state == "pending":
        custom_api_instance.patch_namespaced_custom_object(
            group="resourcemanagement-v.io",
            version="v1",
            plural="agents",
            namespace=agent.namespace,
            name=agent.name,
            body={
                "spec": {"status": "applied"},
            },
        )
        logger.warning(
            "Agent %s flagged as APPLIED because it was in PENDING state",
            agent.name,
        )

    if not patched and agent.state == "pending":
        iteration_history = iteration_conflict_dict.get(agent.name, [])
        if (
            len(iteration_history) > 3
            and iteration_history[list(iteration_history.keys())[-1]]
            == iteration_history[list(iteration_history.keys())[-2]]
            == iteration_history[list(iteration_history.keys())[-3]]
        ):
            custom_api_instance.patch_namespaced_custom_object(
                group="resourcemanagement-v.io",
                version="v1",
                plural="agents",
                namespace=agent.namespace,
                name=agent.name,
                body={"spec": {"status": "applied"}},
            )
            logger.warning("Flagged as APPLIED")

    if patched:
        updated_restart_count = agent.restarts + 1
        custom_api_instance.patch_namespaced_custom_object(
            group="resourcemanagement-v.io",
            version="v1",
            plural="agents",
            namespace=agent.namespace,
            name=agent.name,
            body={"spec": {"restarts": updated_restart_count}},
        )
        agent.restarts = updated_restart_count

    try:
        if agent.name in iteration_conflict_dict:
            iteration_history = iteration_conflict_dict[agent.name]

            if patched and agent.restarts != 0 and len(iteration_history) > 2:
                if (
                    iteration_history[list(iteration_history.keys())[-1]]
                    == (iteration_history[list(iteration_history.keys())[-2]] + 1)
                    == (iteration_history[list(iteration_history.keys())[-3]] + 2)
                ):
                    logger.info("Conflict detected for %s", agent.name)
                    custom_api_instance.patch_namespaced_custom_object(
                        group="resourcemanagement-v.io",
                        version="v1",
                        plural="agents",
                        namespace=agent.namespace,
                        name=agent.name,
                        body={"spec": {"status": "conflicting"}},
                    )
                    logger.warning("Flagged as CONFLICTING")

            if agent.state == "conflicting" and len(iteration_history) > 2:
                if (
                    iteration_history[list(iteration_history.keys())[-1]]
                    == iteration_history[list(iteration_history.keys())[-2]]
                    == iteration_history[list(iteration_history.keys())[-3]]
                ):
                    custom_api_instance.patch_namespaced_custom_object(
                        group="resourcemanagement-v.io",
                        version="v1",
                        plural="agents",
                        namespace=agent.namespace,
                        name=agent.name,
                        body={"spec": {"status": "applied"}},
                    )
                    logger.warning("Flagged as APPLIED")
    except Exception as e:
        logger.error("Conflict Testing Error: %s", e)
    logger.warning("Done applying")


def apply_agent_to_deployment(
    checker,
    agent,
    api_instance,
    deployment=None,
    quota_usage=None,
    cpu_quota_usage=None,
):
    """
    Applies the agent specification to a deployment, updating resource limits and requests.

    Args:
        checker (str): The deployment name.
        agent (AgentDTO): The agent specification containing resource specifications.
        api_instance (AppsV1Api): The Kubernetes API instance.
        pod (object, optional): The pod object, if applicable.
        quota_usage (str, optional): The memory quota usage to apply.
        cpu_quota_usage (float, optional): The CPU quota usage to apply.
    """
    logging.info(
        "Applying agent specification to deployment %s in namespace %s",
        checker,
        agent.namespace,
    )
    if quota_usage or cpu_quota_usage:
        deployment = api_instance.read_namespaced_deployment(
            checker, deployment.namespace
        )
    else:
        deployment = api_instance.read_namespaced_deployment(checker, agent.namespace)

    for container in deployment.spec.template.spec.containers:

        if agent.resources.resourceQuota:
            if agent.resources.resourceQuota.cpu != 1:
                container.resources.limits["cpu"] = cpu_quota_usage
                container.resources.requests["cpu"] = cpu_quota_usage
            if agent.resources.resourceQuota.memory != 1:
                container.resources.limits["memory"] = quota_usage
                container.resources.requests["memory"] = quota_usage

        logging.info("Updated container resources")

    # Patch the deployment with updated resources
    api_instance.patch_namespaced_deployment(
        name=deployment.metadata.name, namespace=agent.namespace, body=deployment
    )
    logging.info("Patched deployment %s", deployment.metadata.name)


def get_deployment_object(name, namespace, api_instance):
    deployment = api_instance.read_namespaced_deployment(name, namespace)
    return deployment


def check_pods_on_node(deployment_name, namespace, node_name):
    v1 = client.CoreV1Api()
    pods = v1.list_namespaced_pod(namespace=namespace)

    count = 0
    for pod in pods.items:
        if (
            pod.metadata.labels.get("app") == deployment_name
            and pod.spec.node_name == node_name
        ):
            count += 1
    return count


def check_deployments_for_agent_scope(df, agent, api_instance):

    if agent.scope == "node":
        df[agent.name + "_replicas_on_node"] = 0  # Dynamically add column for agent
        for index, row in df.iterrows():
            replicas_on_node = check_pods_on_node(
                row["deployment_name"], row["namespace"], agent.node_name
            )
            df.at[index, agent.name + "_replicas_on_node"] = replicas_on_node
            df_target = df[df[agent.name + "_replicas_on_node"] > 0]
    if agent.scope == "namespace":
        df_target = df[df["namespace"] == agent.namespace]
    if agent.scope == "deployment":
        # Filter the DataFrame to include only the specific deployment
        df_target = df[df["deployment_name"] == agent.deployment_name]

    return df, df_target


def initialize_deployment_df():
    columns = [
        "deployment_name",
        "namespace",
        "replicas",
        "node",
        "scope",
        "applied_agents",
        "action",
        "initial_cpu_usage",
        "applied_cpu_usage",
        "initial_memory_usage",
        "latest_memory_usage",
        "applied_memory_usage",
        "initial_avg_latency",
        "latest_avg_latency",
        "avg_memory_usage_30m",
        "avg_memory_usage_3h",
        "avg_memory_usage_24h",
        "avg_cpu_usage_30m",
        "avg_cpu_usage_3h",
        "avg_cpu_usage_24h",
    ]
    df = pd.DataFrame(columns=columns)
    return df


def get_cluster_deployments(df):
    logging.info("Fetching deployments")
    v1 = client.CoreV1Api()
    apps_v1 = client.AppsV1Api()
    deployments = []
    target_namespaces = rms_config.get("target_namespaces", ["sim-env"])

    # Get all deployments in the target namespaces
    for namespace in target_namespaces:
        try:
            namespace_deployments = apps_v1.list_namespaced_deployment(
                namespace=namespace
            )
            deployments.extend(
                namespace_deployments.items
            )  # Append deployments from this namespace
        except client.exceptions.ApiException as e:
            logging.error(
                "Error fetching deployments in namespace %s: %s", namespace, e
            )
            continue

    # Create a list to store deployment information
    deployment_info = []
    for deployment in deployments:
        deployment_info.append(
            {
                "deployment_name": deployment.metadata.name,
                "namespace": deployment.metadata.namespace,
            }
        )

    logging.debug("Initialized DataFrame in get_node_resources")
    deployment_df = pd.DataFrame(deployment_info)
    # Filter out rows that already exist in the original DataFrame
    existing_deployments = set(zip(df["deployment_name"], df["namespace"]))
    new_rows = deployment_df[
        ~deployment_df.apply(
            lambda row: (row["deployment_name"], row["namespace"])
            in existing_deployments,
            axis=1,
        )
    ]
    logging.info("Filtered new rows to be added: %s", new_rows)

    # Append new rows to the existing DataFrame
    df = pd.concat([df, new_rows], ignore_index=True)

    # Remove rows for deployments that are no longer present
    existing_deployments = set(
        zip(deployment_df["deployment_name"], deployment_df["namespace"])
    )
    # Filter df to only include deployments that are in deployment_df
    df = df[df["deployment_name"].isin(deployment_df["deployment_name"])]

    for deployment in deployments:
        df.loc[df["deployment_name"] == deployment.metadata.name, "replicas"] = (
            deployment.spec.replicas
        )

    # Log the removed deployments
    removed_deployments = set(df["deployment_name"]) - set(
        deployment_df["deployment_name"]
    )
    for deployment in removed_deployments:
        logging.info("Removed deployment %s from DataFrame", deployment)

    update_deployment_resources(df, new_rows)

    logging.info("Completed fetching resources")

    return df


def update_deployment_resources(df, new_rows, agent=None):
    for index, row in df.iterrows():
        if pd.isna(row["deployment_name"]):
            latest_memory_usage, avg_mem_30m, avg_mem_3h, avg_mem_24h = (
                query_memory_usage(row["deployment_name"])
            )
            latest_cpu_usage, avg_cpu_30m, avg_cpu_3h, avg_cpu_24h = query_cpu_usage(
                row["deployment_name"]
            )
        else:
            latest_memory_usage, avg_mem_30m, avg_mem_3h, avg_mem_24h = (
                query_memory_workload_usage(
                    row["deployment_name"], row["namespace"], "deployment"
                )
            )
            latest_cpu_usage, avg_cpu_30m, avg_cpu_3h, avg_cpu_24h = (
                query_cpu_workload_usage(
                    row["deployment_name"], row["namespace"], "deployment"
                )
            )
            avg_latency = query_avg_latency_1h(row["deployment_name"])
            latest_latency = query_avg_latency_latest(row["deployment_name"])

        logging.debug(
            "Resource usage for deployment %s: Memory - %s, %s, %s, %s; CPU - %s, %s, %s, %s",
            row["deployment_name"],
            latest_memory_usage,
            avg_mem_30m,
            avg_mem_3h,
            avg_mem_24h,
            latest_cpu_usage,
            avg_cpu_30m,
            avg_cpu_3h,
            avg_cpu_24h,
        )
        if pd.isna(row["action"]):
            df.at[index, "initial_memory_usage"] = latest_memory_usage
            df.at[index, "initial_avg_latency"] = avg_latency
            df.at[index, "initial_cpu_usage"] = avg_cpu_30m
            df.at[index, "applied_agents"] = []
        df.at[index, "latest_memory_usage"] = latest_memory_usage
        df.at[index, "avg_memory_usage_30m"] = avg_mem_30m
        df.at[index, "avg_memory_usage_3h"] = avg_mem_3h
        df.at[index, "avg_memory_usage_24h"] = avg_mem_24h
        df.at[index, "avg_cpu_usage_30m"] = avg_cpu_30m
        df.at[index, "avg_cpu_usage_3h"] = avg_cpu_3h
        df.at[index, "avg_cpu_usage_24h"] = avg_cpu_24h
        df.at[index, "latest_avg_latency"] = latest_latency


def replace_nan_with_none(data):
    if isinstance(data, dict):
        return {k: replace_nan_with_none(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_nan_with_none(v) for v in data]
    elif isinstance(data, float) and np.isnan(data):
        return None
    else:
        return data


def create_json_data(df, json_data):
    apps_v1 = client.AppsV1Api()

    for index, deployment in df.iterrows():
        deployment_obj = apps_v1.read_namespaced_deployment(
            deployment["deployment_name"], deployment["namespace"]
        )
        if deployment_obj is None or deployment_obj.metadata is None:
            logging.warning(
                "Skipping object %s in namespace %s due to missing deployment object or metadata",
                deployment["deployment_name"],
                deployment["namespace"],
            )
            continue
        # Prepare input data for the model
        input_data = {
            "deployment_id": deployment["deployment_name"],
            "replicas": int(deployment["replicas"]),
            "initial_cpu_usage": (
                round(deployment["initial_cpu_usage"], 2)
                if deployment["initial_cpu_usage"] is not None
                else None
            ),
            "applied_cpu_usage": (
                round(deployment["applied_cpu_usage"], 2)
                if deployment["applied_cpu_usage"] is not None
                else None
            ),
            "initial_memory_usage": (
                round(deployment["initial_memory_usage"], 2)
                if deployment["initial_memory_usage"] is not None
                else None
            ),
            "applied_memory_usage": (
                deployment["applied_memory_usage"]
                if deployment["applied_memory_usage"] is not None
                else None
            ),
            "initial_avg_latency": (
                round(deployment["initial_avg_latency"], 2)
                if deployment["initial_avg_latency"] is not None
                else None
            ),
            "latest_avg_latency": (
                round(deployment["latest_avg_latency"], 2)
                if deployment["latest_avg_latency"] is not None
                else None
            ),
            "action": (
                deployment["action"] if deployment["action"] is not None else None
            ),
        }

        # float(quota_usage_str.replace('Mi', ''))
        json_data[deployment["deployment_name"]] = input_data

    # Replace NaN values with None
    json_data = replace_nan_with_none(json_data)

    return json_data


def revert_deployment_to_initial(deployment_df, api_instance, agent_request=False):
    try:
        # get deployment object
        deployment = api_instance.read_namespaced_deployment(
            deployment_df["deployment_name"], deployment_df["namespace"]
        )

        # Save deployment name and namespace as variables
        deployment_name = deployment.metadata.name
        deployment_namespace = deployment.metadata.namespace

        # Fetch the last applied configuration from the deployment's annotations
        annotations = deployment.metadata.annotations or {}
        last_applied_config_json = annotations.get(
            "kubectl.kubernetes.io/last-applied-configuration"
        )

        if not last_applied_config_json:
            logger.warning(
                "No last applied configuration found for deployment %s", deployment_name
            )
            return

        last_applied_config = json.loads(last_applied_config_json)

        if agent_request == True:
            annotations["resource-management/priority"] = "high"
            annotations["processed-by-agent"] = ""
        else:
            annotations["processed-by-agent"] = ""
            annotations["resource-management/priority"] = ""
        last_applied_config["metadata"]["annotations"] = annotations

        # Apply the patch to revert the deployment
        api_instance.patch_namespaced_deployment(
            name=deployment_name,
            namespace=deployment_namespace,
            body=last_applied_config,
        )
        logger.info(
            "Reverted deployment %s to its last applied configuration", deployment_name
        )
    except client.exceptions.ApiException as e:
        logger.error(
            "API exception while reverting deployment %s: %s", deployment_name, e.reason
        )
    except Exception as e:
        logger.error(
            "Unexpected error while reverting deployment %s: %s",
            deployment_name,
            str(e),
        )


def create_agent_DTO(agentorder):
    metadata = agentorder.get("metadata", {})
    spec = agentorder.get("spec", {})

    name = metadata.get("name")
    state = spec.get("status")
    scope = spec.get("scope")
    node_name = spec.get("nodeName")
    namespace = metadata.get("namespace")
    deployment_name = spec.get("deploymentName")
    resources = spec.get("resources", {})
    quotas = resources.get("resourceQuota", {})
    restarts = spec.get("restarts")
    resource_quotas = ResourceQuota(cpu=quotas.get("cpu"), memory=quotas.get("memory"))
    resources_obj = Resources(
        resourceQuota=resource_quotas,
    )
    age = metadata.get("creationTimestamp")
    if age:
        age = pd.to_datetime(age)  # Converts the string to a timezone-aware datetime
        now = pd.Timestamp.now(tz="UTC")  # Make the current timestamp timezone-aware
        age = (now - age).total_seconds() / 3600  # Calculate the age in hours
    else:
        age = None
    return AgentDTO(
        scope=scope,
        name=name,
        state=state,
        node_name=node_name,
        namespace=namespace,
        deployment_name=deployment_name,
        resources=resources_obj,
        restarts=restarts,
        age=age,
    )


def check_performance_of_optimized_deployment(
    api_instance,
    core_v1,
    deployment,
    label_selector,
    agent,
    model_feedback_endpoint,
):
    bad_prediction = False

    try:
        pods = core_v1.list_namespaced_pod(
            deployment["namespace"], label_selector=label_selector
        )
        if pods is None or pods.items is None:
            logger.warning(
                "- OOM-KILLED - Skipping deployment %s in namespace %s due to missing pods",
                deployment["deployment_name"],
                deployment["namespace"],
            )
            return True
        # OOMKill check
        for pod in pods.items:

            pod_status = core_v1.read_namespaced_pod_status(
                pod.metadata.name, pod.metadata.namespace
            )
            if (
                pod_status is None
                or pod_status.status is None
                or pod_status.status.container_statuses is None
            ):
                continue
            if any(
                container_state.last_state.terminated
                and container_state.last_state.terminated.reason == "OOMKilled"
                for container_state in pod_status.status.container_statuses
            ):
                logger.warning(
                    "- OOM-KILLED - Pod %s in namespace %s excluded from quota reduction due to OOMKilled state",
                    pod.metadata.name,
                    pod.metadata.namespace,
                )
                bad_prediction = True
                reward = minimum

        # Latency check
        initial_avg_latency = deployment["initial_avg_latency"]
        latest_avg_latency = deployment["latest_avg_latency"]
        if latest_avg_latency == -1:
            bad_prediction = True
        deployment_obj = get_deployment_object(
            deployment["deployment_name"], deployment["namespace"], api_instance
        )
        if deployment_obj.metadata.labels:
            latency_threshold = deployment_obj.metadata.labels.get(
                "custom-latency-threshold"
            )
            try:
                if latency_threshold:
                    latency_threshold = float(
                        latency_threshold
                    )  # Convert to float if it's a string
                else:
                    latency_threshold = config_latency_threshold
            except ValueError:
                logger.warning(
                    "Invalid custom-latency-threshold value: %s. Falling back to default.",
                    latency_threshold,
                )
                latency_threshold = config_latency_threshold
        else:
            latency_threshold = config_latency_threshold

        if latest_avg_latency >= initial_avg_latency * latency_threshold:
            # Calculate percentage increase
            latency_increase_percentage = (
                (latest_avg_latency - initial_avg_latency) / initial_avg_latency
            ) * 100

            # Scale the reward negatively based on the percentage increase
            reward = max(minimum, -1 * (latency_increase_percentage / 20))
            bad_prediction = True

        if bad_prediction:
            suggested_action = deployment["action"]
            if suggested_action == 0:
                correct_label = 1
            elif (
                suggested_action == 1 or suggested_action == 3 or suggested_action == 2
            ):  # 1 Service might not perform well on the node it was moved to. 3 Service might have too few replicas so optimize normally
                correct_label = 0

            # latency checks
            feedback_data = {
                "id": deployment.deployment_name,
                "correct_label": correct_label,
                "reward": reward,
            }

            try:
                logger.info(
                    "Bad performing deployment. Sending feedback to model: %s",
                    feedback_data,
                )
                print(feedback_data)
                response = requests.post(model_feedback_endpoint, json=feedback_data)
                response.raise_for_status()
                prediction = response.json().get("predicted_class")
                feedback_sent = True
            except requests.exceptions.RequestException as e:
                feedback_sent = False
                logger.error("Error calling model endpoint: %s", e)
            if feedback_sent:
                logger.info("Feedback sent to model: %s", feedback_data)
            revert_deployment_to_initial(deployment, api_instance, True)
            logger.info(
                " Deployment %s in namespace %s labeled as resource-management/priority: high",
                deployment["deployment_name"],
                deployment["namespace"],
            )
            return True
        return False

    except Exception as e:
        logger.error("- OOM-KILLED - Error in handle_oom_killed: %s", e)


def update_resource_usage(
    resource_type,
    quota_factor,
    initial_usage_key,
    applied_usage_key,
    quota_suffix,
    deployment,
    json_data,
    df_target,
    index,
    multiplier=1,
):
    expected_usage = deployment[initial_usage_key] * quota_factor
    print(f"Expected {resource_type.upper()} Usage:", expected_usage)

    if resource_type == "cpu":
        formatted_usage = f"{math.ceil(expected_usage * multiplier)}{quota_suffix}"
        usage_number = expected_usage
    else:  # memory
        formatted_usage = f"{math.ceil(expected_usage)}{quota_suffix}"
        usage_number = math.ceil(expected_usage)

    if (
        pd.isna(deployment[applied_usage_key])
        or deployment[applied_usage_key] != formatted_usage
    ):
        print(f"{applied_usage_key} is na or different from expected")
        change_in_usage = True
        if not pd.isna(deployment["deployment_name"]):
            df_target.loc[index, applied_usage_key] = usage_number

    if deployment["deployment_name"] in json_data:
        json_data[deployment["deployment_name"]][applied_usage_key] = usage_number
    return formatted_usage, usage_number, json_data, change_in_usage


def evict_pod_from_node(agent, core_v1, api_instance, deployment):
    logger.warning("Evicting pod from node %s", agent.node_name)
    nodes = core_v1.list_node()
    node_selector = None
    for node in nodes.items:
        nodename = node.metadata.labels.get("kubernetes.io/hostname")
        taints = node.spec.taints if node.spec.taints else []
        # Check if the node has the taint node.kubernetes.io/unreachable:NoSchedule
        has_unreachable_taint = any(taint.effect == "NoSchedule" for taint in taints)
        if (
            nodename != agent.node_name
            and nodename != "v-dev-research-worker-wwj6n-6w9fx "
            and not has_unreachable_taint
        ):
            node_selector = nodename
            break
    print("NODE SELECTOR", node_selector)

    if node_selector:
        body = {
            "spec": {
                "template": {
                    "spec": {
                        "affinity": {
                            "nodeAffinity": {
                                "requiredDuringSchedulingIgnoredDuringExecution": {
                                    "nodeSelectorTerms": [
                                        {
                                            "matchExpressions": [
                                                {
                                                    "key": "kubernetes.io/hostname",
                                                    "operator": "NotIn",
                                                    "values": [agent.node_name],
                                                }
                                            ]
                                        }
                                    ]
                                },
                                "preferredDuringSchedulingIgnoredDuringExecution": [
                                    {
                                        "weight": 1,
                                        "preference": {
                                            "matchExpressions": [
                                                {
                                                    "key": "kubernetes.io/hostname",
                                                    "operator": "In",
                                                    "values": [node_selector],
                                                }
                                            ]
                                        },
                                    }
                                ],
                            }
                        },
                        "nodeSelector": None,
                    }
                }
            }
        }
        print("evicting")
        api_instance.patch_namespaced_deployment(
            name=deployment["deployment_name"],
            namespace=deployment["namespace"],
            body=body,
        )
        logger.warning(
            "Patched deployment %s in namespace %s with node %s",
            deployment["deployment_name"],
            deployment["namespace"],
            node_selector,
        )
